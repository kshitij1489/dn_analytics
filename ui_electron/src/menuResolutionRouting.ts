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

export type MergeTargetCandidate = {
    menu_item_id: string;
    is_verified: boolean;
    is_globally_linked?: boolean;
};

/**
 * Which menu items may receive this source variant.
 *
 * `sourceHasCompleteGlobalIdentity` follows the source item + variant pair. A
 * complete source takes the direct global-mutation path, where an unlinked
 * target would be rejected at preview time. An incomplete source takes the
 * coverage-repair path, which establishes target identity too. `is_verified`
 * cannot stand in for link coverage: the global catalog owns the flag now, so a
 * row can read verified and still hold no link.
 *
 * The item being resolved is always eligible — picking it just moves this source
 * variant onto another variant of the same item, and it may legitimately be
 * unverified while sibling variants are still unresolved.
 */
export function isEligibleMergeTarget(
    candidate: MergeTargetCandidate,
    sourceMenuItemId: string,
    sourceHasCompleteGlobalIdentity: boolean,
): boolean {
    if (candidate.menu_item_id === sourceMenuItemId) return true;
    if (!candidate.is_verified) return false;
    return !sourceHasCompleteGlobalIdentity || candidate.is_globally_linked === true;
}

export type MergeTargetVariantCandidate = {
    is_globally_linked?: boolean;
};

/**
 * A direct global variant merge needs canonical identity on both variants.
 * Coverage repair can establish missing identity only while the source pair is
 * incomplete, so otherwise the dropdown must fail closed on an unlinked target.
 */
export function isEligibleMergeTargetVariant(
    candidate: MergeTargetVariantCandidate,
    sourceHasCompleteGlobalIdentity: boolean,
): boolean {
    return !sourceHasCompleteGlobalIdentity || candidate.is_globally_linked === true;
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

export type TargetCoverageContext = {
    globalItemId?: string | null;
    globalVariantId?: string | null;
    locatorCount: number;
};

export type TargetCoveragePlan = {
    mapLocators: boolean;
    mapAtItemLevel: boolean;
};

/**
 * What coverage repair still owes the merge target.
 *
 * The decision turns on whether the target *pair* carries POS evidence of its
 * own, which is what `locatorCount` reports. Two different situations produce
 * none, and they want the same repair: the operator picked a variant the target
 * item does not carry yet (“Create new variant type”, or any variant type the
 * dropdown offers from the database at large), or the pair exists but holds only
 * synthetic, non-POS rows. Either way there is no locator that could carry a
 * variant-level mapping, so no variant link is owed — one cannot be established
 * without evidence. What can still be owed is the target *item*: while it
 * carries no global identity, its own locators must map at item level.
 */
export function targetCoveragePlan(
    target: TargetCoverageContext,
    creatingNewVariant: boolean,
): TargetCoveragePlan {
    const pairHasOwnPosEvidence = !creatingNewVariant && target.locatorCount > 0;
    return {
        mapLocators: !target.globalItemId || (
            pairHasOwnPosEvidence && !target.globalVariantId
        ),
        mapAtItemLevel: !pairHasOwnPosEvidence,
    };
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
