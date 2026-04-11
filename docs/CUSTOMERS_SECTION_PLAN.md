# Customers Section Plan

## Goal

Add a new `Customers` button in the left navigation above `Orders` as a placeholder now, then expand it into a dedicated customer analytics section with:

- Address book
- Customer order history
- Customer analytics
- Similar user suggestions
- Customer merge and undo-merge workflows

## Status Summary

- `Phase 1`: Completed
- `Phase 2`: Completed
- `Phase 3`: Completed
- `Phase 4`: Completed
- `Phase 5`: Completed
- `Phase 6`: Completed

## Current State

There is now a top-level `Customers` section in the left navigation.

Customer functionality is now primarily available from the `Customers` page:

- `Overview`: customer table
- `Profiles`: customer profile search and detail view
- `Analytics`: customer retention and top-customer analytics
- `Similar Users`: basic ML suggestion queue plus manual pair review
- `Merge History`: merge audit trail with undo

Customer functionality is no longer duplicated inside `Orders` or `Insights`.

- Left navigation is defined in `ui_electron/src/App.tsx`
- Reusable customer profile UI already exists in `ui_electron/src/components/CustomerProfile.tsx`

There is already useful backend support in place:

- Customer search endpoint: `GET /api/orders/customers/search`
- Customer profile endpoint: `GET /api/orders/customers/{customer_id}/profile`
- Customer data query logic: `src/core/queries/customer_queries.py`
- Customer similarity suggestions endpoint: `GET /api/orders/customers/similar`
- Customer merge preview endpoint: `GET /api/orders/customers/merge/preview`
- Customer merge history endpoint: `GET /api/orders/customers/merge/history`
- Customer merge / undo endpoints: `POST /api/orders/customers/merge`, `POST /api/orders/customers/merge/undo`

## Recommendation

Create one top-level `Customers` section in the left nav and make it the single home for all customer-related workflows over time. This is cleaner than keeping customer functionality split between `Insights` and `Orders`.

## Phase 1: Placeholder Nav Button

Status: Completed

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
- At the time of Phase 1, existing `Orders` and `Insights` behavior remained unchanged until later cleanup phases

Delivered:

- Added `Customers` to the left navigation above `Orders`
- Added top-level `customers` page routing
- Added dedicated `Customers` page scaffolding and placeholders

## Phase 2: Consolidate Existing Customer Features

Status: Completed

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

Delivered:

- Added top-level `Customers` sub-sections for `Overview`, `Profiles`, and `Analytics`
- Moved customer overview into the new `Customers` page
- Moved customer profile search/detail flow into the new `Customers` page
- Moved customer analytics into the new `Customers` page
- Updated customer links to deep-link into `Customers` profiles
- Added placeholder sections for `Similar Users` and `Merge History`

## Phase 3: Address Book Expansion

Status: Completed

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

Delivered:

- Exposed `customers.address` in the customer profile API/UI for backward compatibility
- Added a dedicated `customer_addresses` table for future multi-address support
- Added migration-safe backfill from legacy `customers.address` into `customer_addresses`
- Updated customer profile responses to include structured addresses plus a primary-address summary
- Rendered an address-book view inside `Customers > Profiles`

## Phase 4: Similar User Suggestions and Merge Workflow

Status: Completed

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

Delivered:

- Replaced the `Similar Users` placeholder with a basic duplicate-suggestion queue
- Added a basic ML similarity model using text vectorization plus nearest-neighbor scoring
- Added side-by-side merge preview in the `Customers` page
- Added operator-driven manual pair selection for merge review beyond the suggestion queue
- Implemented customer merge and undo-merge backend workflows
- Added customer merge audit history with undo from the UI
- Kept merged source customers hidden from active customer search/overview while allowing undo to restore them

## Phase 5: Navigation and Deep Linking

Status: Completed

The app already supports navigation params through `ui_electron/src/contexts/NavigationContext.tsx`.

After the new `Customers` page is introduced:

- Update customer links to navigate to `customers` instead of `orders`
- Preserve deep-linking into a specific customer profile using `customerId`
- Keep direct navigation from analytics tables and order/customer rows

Delivered so far:

- Customer links now navigate into the top-level `Customers` page
- Profile deep-linking is active for customer profile navigation
- The old `Orders`-specific customer deep-link shape is no longer used by in-repo navigation

## Phase 6: Cleanup After Migration

Status: Completed

Once the `Customers` page is fully functional:

- Remove or reduce the customer tab inside `Orders`
- Remove or reduce the customer tab inside `Insights`
- Keep a single canonical customer experience in the left nav

Delivered:

- Removed the customer tab from `Orders`
- Removed the customer tab from `Insights`
- Kept `Customers` as the single canonical customer workspace in the left navigation

## Suggested Delivery Order

1. Completed: Add the new left-nav `Customers` button and placeholder page
2. Completed: Move profile search and customer overview into `Customers`
3. Completed: Move customer analytics into `Customers`
4. Completed: Add backward-compatible address support plus structured address-book storage
5. Completed: Replace `Similar Users` placeholder content with a real review queue
6. Completed: Design and implement customer merge plus undo-merge backend workflow
7. Completed (basic): Connect a first-pass ML similarity model to drive suggestions

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

Refine Phase 4:

- Improve suggestion precision and false-positive handling for anonymous customers
- Decide whether to evolve the basic model into rule-tuned scoring, clustering, or a stronger supervised duplicate detector
