import axios from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
    ALL_STORES_SCOPE,
    api,
    endpoints,
    isAllStoresScope,
    isAnalyticsScopeLocked,
    setActiveAnalyticsScope,
    subscribeAnalyticsScopeLock,
    subscribeScopeCompleteness,
    type ScopeCompleteness,
} from './api';

/**
 * Drives the request/response interceptors in `api.ts` through a stub adapter so
 * the restaurant-scope guards are pinned without a backend:
 *   - every request carries the selected `X-Analytics-Scope`
 *   - a response that arrives after the user switched restaurants is discarded
 *     rather than rendered against the new store's page
 *   - a mutation awaiting its central commit locks the selector; a Sync DB run
 *     and plain reads do not
 */

type Pending = {
    config: any;
    resolve: (value: unknown) => void;
    reject: (reason?: unknown) => void;
};

let pending: Pending[] = [];

/** Adapter that never settles on its own — the test decides when each call returns. */
function deferredAdapter(config: any) {
    return new Promise((resolve, reject) => {
        pending.push({
            config,
            resolve: (data) => resolve({ data, status: 200, statusText: 'OK', headers: {}, config }),
            // Real adapters attach the request config to their rejection; mirror
            // that so the generation guard sees the same shape it does in the app.
            reject: (reason) => reject(
                new axios.AxiosError(String(reason), 'ERR_BAD_RESPONSE', config, null, undefined),
            ),
        });
    });
}

api.defaults.adapter = deferredAdapter as any;

/** Axios runs interceptors on the microtask queue, so let the chain reach the adapter. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Start a request and swallow its rejection; the test asserts on the returned promise. */
function start<T extends Promise<unknown>>(request: T): T {
    request.catch(() => undefined);
    return request;
}

afterEach(async () => {
    pending.forEach((call) => call.resolve(null));
    await flush();
    pending = [];
    setActiveAnalyticsScope(null);
    vi.unstubAllGlobals();
});

describe('analytics scope propagation', () => {
    it('sends the selected restaurant on every request', async () => {
        setActiveAnalyticsScope('rest-A');
        const request = start(api.get('/insights/kpis'));
        await flush();

        expect(pending).toHaveLength(1);
        expect(pending[0].config.headers['X-Analytics-Scope']).toBe('rest-A');

        pending[0].resolve({ ok: true });
        await expect(request).resolves.toMatchObject({ data: { ok: true } });
    });

    it('omits the header when no restaurant is selected', async () => {
        setActiveAnalyticsScope(null);
        start(api.get('/insights/kpis'));
        await flush();
        expect(pending[0].config.headers['X-Analytics-Scope']).toBeUndefined();
    });
});

describe('stale response discarding', () => {
    it('discards a read that resolves after the restaurant changed', async () => {
        setActiveAnalyticsScope('rest-A');
        const inFlight = start(api.get('/orders/view'));
        await flush();

        // User switches to B while A's read is still open.
        setActiveAnalyticsScope('rest-B');
        pending[0].resolve({ store: 'A' });

        await expect(inFlight).rejects.toSatisfy(
            (error: unknown) => axios.isCancel(error),
            'expected the stale response to be cancelled, not delivered',
        );
    });

    it('discards a stale error the same way', async () => {
        setActiveAnalyticsScope('rest-A');
        const inFlight = start(api.get('/orders/view'));
        await flush();

        setActiveAnalyticsScope('rest-B');
        pending[0].reject(new Error('backend blew up on A'));

        await expect(inFlight).rejects.toSatisfy((error: unknown) => axios.isCancel(error));
    });

    it('delivers a response when the restaurant did not change', async () => {
        setActiveAnalyticsScope('rest-A');
        const inFlight = start(api.get('/orders/view'));
        await flush();

        pending[0].resolve({ store: 'A' });
        await expect(inFlight).resolves.toMatchObject({ data: { store: 'A' } });
    });

    it('does not bump the generation when the same restaurant is re-selected', async () => {
        setActiveAnalyticsScope('rest-A');
        const inFlight = start(api.get('/orders/view'));
        await flush();

        setActiveAnalyticsScope('rest-A');
        pending[0].resolve({ store: 'A' });
        await expect(inFlight).resolves.toMatchObject({ data: { store: 'A' } });
    });

    it('keeps a captured sync job poll visible after switching restaurants', async () => {
        setActiveAnalyticsScope('rest-A');
        const inFlight = start(api.get('/sync/status/job-A'));
        await flush();

        setActiveAnalyticsScope('rest-B');
        pending[0].resolve({ job_id: 'job-A', status: 'running' });

        await expect(inFlight).resolves.toMatchObject({
            data: { job_id: 'job-A', status: 'running' },
        });
    });
});

describe('stale stream cancellation', () => {
    it('aborts an AI stream when the restaurant changes after headers arrive', async () => {
        let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
        let requestSignal: AbortSignal | undefined;
        vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
            requestSignal = init?.signal as AbortSignal;
            const body = new ReadableStream<Uint8Array>({
                start(controller) {
                    streamController = controller;
                    requestSignal?.addEventListener('abort', () => {
                        controller.error(new DOMException('scope changed', 'AbortError'));
                    });
                },
            });
            return new Response(body, {
                status: 200,
                headers: { 'Content-Type': 'text/event-stream' },
            });
        }));

        setActiveAnalyticsScope('rest-A');
        const response = await endpoints.ai.chatStream({ prompt: 'summary report' });
        const read = response.body!.getReader().read();
        read.catch(() => undefined);

        setActiveAnalyticsScope('rest-B');

        expect(requestSignal?.aborted).toBe(true);
        await expect(read).rejects.toBeTruthy();
        expect(streamController).toBeDefined();
    });
});

describe('selector lock while a mutation is awaiting commit', () => {
    it('locks for a menu mutation and releases when it settles', async () => {
        setActiveAnalyticsScope('rest-A');
        const seen: boolean[] = [];
        const unsubscribe = subscribeAnalyticsScopeLock((locked) => seen.push(locked));

        expect(isAnalyticsScopeLocked()).toBe(false);
        const commit = start(api.post('/menu/merge', { menu_item_ids: ['a', 'b'] }));
        await flush();
        expect(isAnalyticsScopeLocked()).toBe(true);

        pending[0].resolve({ status: 'committed' });
        await commit;

        expect(isAnalyticsScopeLocked()).toBe(false);
        expect(seen).toEqual([true, false]);
        unsubscribe();
    });

    it('releases the lock when the commit fails', async () => {
        setActiveAnalyticsScope('rest-A');
        const commit = start(api.post('/orders/customers/merge', {}));
        await flush();
        expect(isAnalyticsScopeLocked()).toBe(true);

        pending[0].reject(new Error('409 stale revision'));
        await expect(commit).rejects.toThrow();
        expect(isAnalyticsScopeLocked()).toBe(false);
    });

    it('stays locked until the last of several mutations settles', async () => {
        setActiveAnalyticsScope('rest-A');
        const first = start(api.post('/menu/retype', {}));
        const second = start(api.post('/menu/remap', {}));
        await flush();
        expect(isAnalyticsScopeLocked()).toBe(true);

        pending[0].resolve({});
        await first;
        expect(isAnalyticsScopeLocked()).toBe(true);

        pending[1].resolve({});
        await second;
        expect(isAnalyticsScopeLocked()).toBe(false);
    });

    it('does not lock for reads or selecting a restaurant', async () => {
        setActiveAnalyticsScope('rest-A');
        start(api.get('/menu/items'));
        await flush();
        expect(isAnalyticsScopeLocked()).toBe(false);

        start(api.post('/config/stores/select', { restaurant_id: 'rest-B' }));
        await flush();
        expect(isAnalyticsScopeLocked()).toBe(false);
    });

    it('locks Sync DB only until the backend returns the captured job ID', async () => {
        setActiveAnalyticsScope('rest-A');
        const startSync = start(api.post('/sync/run', { restaurant_id: 'rest-A' }));
        await flush();
        expect(isAnalyticsScopeLocked()).toBe(true);

        pending[0].resolve({ job_id: 'job-A' });
        await startSync;
        expect(isAnalyticsScopeLocked()).toBe(false);

        start(api.get('/sync/status/job-A'));
        await flush();
        expect(isAnalyticsScopeLocked()).toBe(false);
    });

    it('locks for a destructive profile operation', async () => {
        setActiveAnalyticsScope('rest-A');
        start(api.post('/config/reset-db', { section: 'orders' }));
        await flush();
        expect(isAnalyticsScopeLocked()).toBe(true);
    });
});

describe('All Stores scope', () => {
    it('sends the local all-stores token as the scope header', async () => {
        setActiveAnalyticsScope(ALL_STORES_SCOPE);
        start(api.get('/insights/kpis'));
        await flush();

        expect(pending[0].config.headers['X-Analytics-Scope']).toBe('__all__');
        expect(isAllStoresScope()).toBe(true);
    });

    it('unwraps the federation envelope and publishes completeness', async () => {
        setActiveAnalyticsScope(ALL_STORES_SCOPE);
        const seen: (ScopeCompleteness | null)[] = [];
        const unsubscribe = subscribeScopeCompleteness((state) => seen.push(state));

        const request = start(api.get('/insights/kpis'));
        await flush();
        pending[0].resolve({
            scope: 'all',
            profiles_requested: 2,
            profiles_included: 1,
            incomplete_profiles: [{ restaurant_id: 'rest-B', restaurant_name: 'Two', error: 'locked' }],
            excluded_profiles: [],
            stores: [{ restaurant_id: 'rest-A', restaurant_name: 'One' }],
            identity_coverage: {
                global_mode_active: true,
                global_aggregation_active: false,
                global_only: true,
                linked: 7,
                total: 9,
                linked_rows: 7,
                total_rows: 9,
                omitted_unlinked_rows: 2,
                quarantine_count: 0,
            },
            data: { total_revenue: 400 },
        });

        // Pages keep reading the single-store shape…
        await expect(request).resolves.toMatchObject({ data: { total_revenue: 400 } });
        // …while the unreadable store stays visible instead of counting as zero.
        expect(seen.at(-1)).toMatchObject({
            profilesRequested: 2,
            profilesIncluded: 1,
            incompleteProfiles: [{ restaurant_id: 'rest-B' }],
            identityCoverage: {
                global_aggregation_active: false,
                global_only: true,
                linked: 7,
                total: 9,
                omitted_unlinked_rows: 2,
            },
        });
        unsubscribe();
    });

    it('leaves a single-store response untouched', async () => {
        setActiveAnalyticsScope('rest-A');
        const request = start(api.get('/insights/kpis'));
        await flush();
        pending[0].resolve({ total_revenue: 100, scope: undefined });
        await expect(request).resolves.toMatchObject({ data: { total_revenue: 100 } });
    });

    it('discards in-flight reads across All → A → B → All switching', async () => {
        const results: string[] = [];

        setActiveAnalyticsScope(ALL_STORES_SCOPE);
        const allRead = start(api.get('/insights/kpis'));
        await flush();

        setActiveAnalyticsScope('rest-A');
        const aRead = start(api.get('/insights/kpis'));
        await flush();

        setActiveAnalyticsScope('rest-B');
        const bRead = start(api.get('/insights/kpis'));
        await flush();

        setActiveAnalyticsScope(ALL_STORES_SCOPE);
        const finalRead = start(api.get('/insights/kpis'));
        await flush();

        // Every earlier scope answers late, in the wrong order.
        pending[0].resolve({ scope: 'all', profiles_requested: 2, profiles_included: 2, data: { label: 'stale-all' } });
        pending[2].resolve({ label: 'stale-B' });
        pending[1].resolve({ label: 'stale-A' });
        pending[3].resolve({ scope: 'all', profiles_requested: 2, profiles_included: 2, data: { label: 'current-all' } });

        for (const [name, request] of [['all', allRead], ['a', aRead], ['b', bRead]] as const) {
            await request.then(
                () => results.push(`${name}:delivered`),
                (error) => results.push(axios.isCancel(error) ? `${name}:discarded` : `${name}:error`),
            );
        }
        expect(results).toEqual(['all:discarded', 'a:discarded', 'b:discarded']);
        await expect(finalRead).resolves.toMatchObject({ data: { label: 'current-all' } });
    });

    it('clears completeness when the scope changes', async () => {
        setActiveAnalyticsScope(ALL_STORES_SCOPE);
        const seen: (ScopeCompleteness | null)[] = [];
        const unsubscribe = subscribeScopeCompleteness((state) => seen.push(state));

        const request = start(api.get('/insights/kpis'));
        await flush();
        pending[0].resolve({
            scope: 'all', profiles_requested: 2, profiles_included: 2,
            incomplete_profiles: [], excluded_profiles: [], stores: [], data: {},
        });
        await request;

        setActiveAnalyticsScope('rest-A');
        expect(seen.at(-1)).toBeNull();
        unsubscribe();
    });
});
