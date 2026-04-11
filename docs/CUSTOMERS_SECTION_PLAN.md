# Customers Section Plan

## Goal

Add a new `Customers` button in the left navigation above `Orders` as a placeholder now, then expand it into a dedicated customer analytics section with:

- Address book
- Customer order history
- Customer analytics
- Similar user suggestions
- Customer merge and undo-merge workflows

## Current State

The current customer-related functionality is split across multiple places:

- Left navigation is defined in `ui_electron/src/App.tsx`
- Customer overview and profile search live inside the `Orders` page
- Customer analytics live inside the `Insights` page
- Reusable customer profile UI already exists in `ui_electron/src/components/CustomerProfile.tsx`

There is already useful backend support in place:

- Customer search endpoint: `GET /api/orders/customers/search`
- Customer profile endpoint: `GET /api/orders/customers/{customer_id}/profile`
- Customer data query logic: `src/core/queries/customer_queries.py`

The database already has a `customers.address` column, but the current customer profile response does not expose address data yet.

There is no dedicated user-merge or undo-merge workflow yet. The future customer section should become the place where this identity-resolution workflow lives.

## Recommendation

Create one top-level `Customers` section in the left nav and make it the single home for all customer-related workflows over time. This is cleaner than keeping customer functionality split between `Insights` and `Orders`.

## Phase 1: Placeholder Nav Button

Scope:

- Add a new `Customers` button above `Orders` in the left nav
- Add a new top-level page render branch for `customers`
- Create a new `ui_electron/src/pages/Customers.tsx` placeholder page

Placeholder page content should communicate the intended direction:

- Customer Analytics
- Address Book
- Order History
- Customer Insights / Loyalty
- Similar Users and Merge Review

Notes:

- This phase is UI-only
- No backend changes are required
- Existing `Orders` and `Insights` behavior can remain unchanged for now

## Phase 2: Consolidate Existing Customer Features

Move existing customer functionality into the new `Customers` page.

Planned internal sub-sections:

- Overview
- Profiles
- Analytics
- Similar Users
- Merge History

Suggested mapping:

- `Overview`: current customers table from the `Orders` page
- `Profiles`: current `CustomerProfile` search and profile detail flow
- `Analytics`: current customer retention and top-customer analytics from the `Insights` page
- `Similar Users`: future review queue for possible duplicate or related users
- `Merge History`: future audit trail with undo support

Result:

- `Customers` becomes the canonical customer area
- `Orders` focuses on orders and order-related entities
- `Insights` focuses on business-wide insights

## Phase 3: Address Book Expansion

Short term:

- Expose the existing `customers.address` field in the customer profile API and UI

Long term:

- If a customer needs multiple saved addresses, create a dedicated `customer_addresses` table instead of storing everything in a single text field

Recommended future address model:

- `address_id`
- `customer_id`
- `label`
- `address_line_1`
- `address_line_2`
- `city`
- `state`
- `postal_code`
- `country`
- `is_default`

## Phase 4: Similar User Suggestions and Merge Workflow

This section should eventually support identity resolution for customers who are likely duplicates or who should belong to an already verified customer profile.

Primary use cases:

- Suggest similar users based on name, phone, address, and order behavior
- Suggest that a newly seen or unverified customer likely belongs to an existing verified user
- Let an operator review the suggestion in the UI before any merge happens

Planned UI capabilities:

- Show a simple placeholder area for `Similar Users` in the new `Customers` page
- Display candidate pairs or groups of customers that may represent the same person
- Show a side-by-side comparison before merge
- Allow merge action from the UI
- Allow undo merge if the merge was done by mistake

Planned backend behavior:

- Merge a source customer into a target customer
- Reassign all related records to the surviving customer ID
- Update customer-level aggregates after merge
- Persist a merge log so the action can be reversed

Tables and data that should be considered in merge handling:

- `customers`
- `orders`
- `order_items`
- `order_taxes`
- `order_discounts`
- Any other related entities that store or derive customer linkage

Undo merge requirements:

- Keep a merge history record with enough information to reverse the operation
- Support restoring the original source customer and related references
- Make undo available from the UI

Model note:

- The exact ML or matching approach is intentionally open for now
- In the plan, keep this as a placeholder for a future model
- Possible future approaches could include rule-based matching, clustering, embedding similarity, or supervised duplicate-detection, but no final choice is required yet

## Phase 5: Navigation and Deep Linking

The app already supports navigation params through `ui_electron/src/contexts/NavigationContext.tsx`.

After the new `Customers` page is introduced:

- Update customer links to navigate to `customers` instead of `orders`
- Preserve deep-linking into a specific customer profile using `customerId`
- Keep direct navigation from analytics tables and order/customer rows

## Phase 6: Cleanup After Migration

Once the `Customers` page is fully functional:

- Remove or reduce the customer tab inside `Orders`
- Remove or reduce the customer tab inside `Insights`
- Keep a single canonical customer experience in the left nav

## Suggested Delivery Order

1. Add the new left-nav `Customers` button and placeholder page
2. Move profile search and customer overview into `Customers`
3. Move customer analytics into `Customers`
4. Add address field support to profile API and UI
5. Add placeholder `Similar Users` and `Merge History` sections
6. Design and implement customer merge plus undo-merge backend workflow
7. Connect a future similarity model or rule engine to drive suggestions
8. Clean up duplicated customer entry points in `Orders` and `Insights`

## Initial Files Likely To Change

- `ui_electron/src/App.tsx`
- `ui_electron/src/pages/Customers.tsx`
- `ui_electron/src/pages/Orders.tsx`
- `ui_electron/src/pages/Insights.tsx`
- `ui_electron/src/components/CustomerLink.tsx`
- `ui_electron/src/components/CustomerProfile.tsx`
- `database/schema_sqlite.sql`
- `src/api/models.py`
- `src/api/routers/orders.py`
- `src/api/routers/config.py`
- `src/core/queries/customer_queries.py`

## Immediate Next Step

Implement Phase 1 only:

- Add the `Customers` nav button above `Orders`
- Create the placeholder `Customers` page
- Include simple placeholders for future `Similar Users` and merge functionality
- Leave deeper customer consolidation for the next pass
