export type GlobalResolutionRoute = 'verify_assignment' | 'locator_map';

export type CanonicalItemTarget = {
    global_menu_item_id: string;
    local_menu_item_id?: string | null;
    canonical_name: string;
    canonical_type: string;
    is_verified: boolean;
};

export type CanonicalVariantTarget = {
    global_variant_id: string;
    local_variant_id?: string | null;
    canonical_name: string;
    unit?: string | null;
    value?: string | number | null;
    is_verified: boolean;
};

/**
 * Global-first resolution targets. Catalog verification and assignment
 * verification are separate concerns: enrollment catalogs may legitimately
 * contain active rows whose catalog flag is still false. Human resolution must
 * therefore search every active canonical row. Rows without a local projection
 * stay visible but are disabled by the UI until Sync DB repairs the cache.
 */
export function filterCanonicalItemTargets<T extends CanonicalItemTarget>(
    items: T[],
    search: string,
): T[] {
    const needle = search.trim().toLowerCase();
    return items.filter(item => !needle || `${item.canonical_name} ${item.canonical_type}`
        .toLowerCase()
        .includes(needle));
}

export function canonicalVariantLabel(variant: CanonicalVariantTarget): string {
    const dimension = [
        variant.unit?.trim(),
        variant.value === null || variant.value === undefined || variant.value === ''
            ? ''
            : String(variant.value),
    ].filter(Boolean).join(' ');
    return dimension ? `${variant.canonical_name} · ${dimension}` : variant.canonical_name;
}

/** Global selectors store only canonical identity; local projection IDs are not choices. */
export function canonicalItemSelectionId(item: CanonicalItemTarget): string {
    return item.global_menu_item_id;
}

export function canonicalVariantSelectionId(variant: CanonicalVariantTarget): string {
    return variant.global_variant_id;
}

type LinkedCanonicalItemContext = {
    global_item_id?: string | null;
    canonical_name: string;
    canonical_type: string;
};

/**
 * A resolution must not reinterpret edits to an already-linked item as a new
 * canonical row. Prefer the active catalog metadata, but retain the resolution
 * context as a fail-closed fallback when the catalog request is unavailable.
 */
export function canonicalItemRenameConflicts(
    context: LinkedCanonicalItemContext,
    catalogItems: CanonicalItemTarget[],
    requestedName: string,
    requestedType: string,
): boolean {
    if (!context.global_item_id) return false;
    const catalogItem = catalogItems.find(
        candidate => candidate.global_menu_item_id === context.global_item_id,
    );
    const currentName = (catalogItem?.canonical_name ?? context.canonical_name).trim();
    const currentType = (catalogItem?.canonical_type ?? context.canonical_type).trim();
    return currentName !== requestedName.trim() || currentType !== requestedType.trim();
}

/** Select a canonical variant only from stable identity, never from its label. */
export function findCanonicalVariantByIdentity<T extends CanonicalVariantTarget>(
    variants: T[],
    currentGlobalVariantId?: string | null,
    suggestedGlobalVariantId?: string | null,
): T | undefined {
    const candidateIds = [currentGlobalVariantId, suggestedGlobalVariantId]
        .map(value => value?.trim())
        .filter((value): value is string => Boolean(value));
    for (const globalVariantId of candidateIds) {
        const match = variants.find(
            variant => variant.global_variant_id === globalVariantId,
        );
        if (match) return match;
    }
    return undefined;
}

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

export type MergeTargetCandidate = {
    menu_item_id: string;
    is_verified: boolean;
};

/**
 * Legacy local resolution targets. Global resolution renders canonical targets
 * through a separate branch, so global-link coverage has no role here.
 */
export function isEligibleMergeTarget(
    candidate: MergeTargetCandidate,
    sourceMenuItemId: string,
): boolean {
    if (candidate.menu_item_id === sourceMenuItemId) return true;
    return candidate.is_verified;
}

export function isResolutionTargetSelectionMissing(
    globalResolutionAdvertised: boolean,
    localTargetId: string,
    globalTargetId: string,
): boolean {
    if (!localTargetId) return true;
    return globalResolutionAdvertised && !globalTargetId;
}

export function mappedVerificationFailureMessage(
    detail: string,
    identityUnresolved: boolean,
): string {
    if (identityUnresolved) {
        return `Global identity mapping succeeded, but assignment verification is still unresolved: ${detail} `
            + 'Sync DB may project newly mapped POS rows. If the resolution remains open afterward, '
            + 'review the mapping rules, especially whether the same POS ID is claimed as both an item and an addon.';
    }
    return `Global identity mapping succeeded, but assignment verification was not confirmed: ${detail} `
        + 'The resolution remains open; retry to verify the existing identities.';
}

/**
 * The canonical row this menu group already owns for the identity key a create
 * asked for, read off that create's preview conflicts.
 *
 * A group cannot hold two rows for one identity key, so a second create is never
 * the right mutation: adopting the row that holds it is what “ensure” means.
 * Without this, a create an earlier attempt already committed makes every retry
 * of the same resolution fail — the preview conflicts, so commit is refused,
 * while the local pair it was minted for still carries no link of its own.
 */
export function claimedCanonicalIdentity(
    conflicts: Array<Record<string, unknown>> | undefined,
    identityField: 'global_item_id' | 'global_variant_id',
): string | null {
    const claimed = (conflicts || [])
        .filter(conflict => conflict?.code === 'canonical_identity_taken')
        .map(conflict => conflict?.[identityField])
        .find(value => typeof value === 'string' && value.trim());
    return typeof claimed === 'string' ? claimed.trim() : null;
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
