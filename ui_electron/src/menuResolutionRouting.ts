export type GlobalResolutionRoute = 'verify_assignment' | 'locator_map';

type Identity = {
    global_item_id?: string | null;
    global_variant_id?: string | null;
};

export type ResolutionAssignmentIdentity = {
    menu_item_id: string;
    source_variant_id?: string | null;
    assignment_order_item_ids?: string[];
};

export function resolutionAttemptKey(item: ResolutionAssignmentIdentity): string {
    const assignmentIds = Array.from(new Set(
        (item.assignment_order_item_ids || [])
            .map(value => String(value || '').trim())
            .filter(Boolean),
    )).sort();
    return [
        String(item.menu_item_id || '').trim(),
        String(item.source_variant_id || '').trim(),
        ...assignmentIds,
    ].join(':');
}

/**
 * Keep the complete create/map/verify attempt single-flight. React state does
 * not update synchronously, so disabling a button alone cannot prevent two
 * same-tick clicks from authoring duplicate global mutations.
 */
export async function runResolutionAttempt<T>(
    inFlight: Set<string>,
    key: string,
    action: () => Promise<T>,
): Promise<T | undefined> {
    if (inFlight.has(key)) return undefined;
    inFlight.add(key);
    try {
        return await action();
    } finally {
        inFlight.delete(key);
    }
}

export class GlobalIdentityMappedVerificationError extends Error {
    readonly verificationError: unknown;

    constructor(verificationError: unknown) {
        super('Global identity mapping succeeded, but assignment verification was not confirmed.');
        this.name = 'GlobalIdentityMappedVerificationError';
        this.verificationError = verificationError;
    }
}

type CoverageRepairSteps<T> = {
    ensureItem: () => Promise<string | null>;
    ensureVariant: (globalItemId: string) => Promise<string | null>;
    mapTargetLocators?: (
        globalItemId: string,
        globalVariantId: string,
    ) => Promise<boolean>;
    mapSourceLocators: (
        globalItemId: string,
        globalVariantId: string,
    ) => Promise<boolean>;
    onIdentityMapped?: (
        globalItemId: string,
        globalVariantId: string,
    ) => void;
    verifyAssignment: (
        globalItemId: string,
        globalVariantId: string,
    ) => Promise<T>;
};

/**
 * Coverage repair is two authorities in sequence: establish global identity,
 * then verify the exact restaurant assignments through the strict menu stream.
 * A source locator-map success is deliberately not a completed resolution.
 */
export async function repairGlobalIdentityCoverage<T>(
    steps: CoverageRepairSteps<T>,
): Promise<T | null> {
    const globalItemId = await steps.ensureItem();
    if (!globalItemId) return null;
    const globalVariantId = await steps.ensureVariant(globalItemId);
    if (!globalVariantId) return null;
    if (steps.mapTargetLocators) {
        const targetMapped = await steps.mapTargetLocators(
            globalItemId,
            globalVariantId,
        );
        if (!targetMapped) return null;
    }
    const sourceMapped = await steps.mapSourceLocators(
        globalItemId,
        globalVariantId,
    );
    if (!sourceMapped) return null;
    steps.onIdentityMapped?.(globalItemId, globalVariantId);
    try {
        return await steps.verifyAssignment(globalItemId, globalVariantId);
    } catch (error) {
        throw new GlobalIdentityMappedVerificationError(error);
    }
}

/**
 * Decide whether a resolution changes canonical identity or only verifies the
 * already-correct restaurant assignment. Shadow capability alone is never a
 * reason to emit a locator-map mutation.
 */
export function globalResolutionRoute(
    resolutionKind: string | null | undefined,
    current: Identity | null | undefined,
    intended: Identity | null | undefined,
): GlobalResolutionRoute {
    const currentItem = current?.global_item_id || '';
    const currentVariant = current?.global_variant_id || '';
    const intendedItem = intended?.global_item_id || '';
    const intendedVariant = intended?.global_variant_id || '';
    if (
        resolutionKind === 'unverified_mapping' &&
        currentItem &&
        currentVariant &&
        currentItem === intendedItem &&
        currentVariant === intendedVariant
    ) {
        return 'verify_assignment';
    }
    return 'locator_map';
}
