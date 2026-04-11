# Customers Section Plan

## Goal

Add a new `Customers` button in the left navigation above `Orders` as a placeholder now, then expand it into a dedicated customer analytics section with:

- Address book
- Customer order history
- Customer analytics

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

Suggested mapping:

- `Overview`: current customers table from the `Orders` page
- `Profiles`: current `CustomerProfile` search and profile detail flow
- `Analytics`: current customer retention and top-customer analytics from the `Insights` page

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

## Phase 4: Navigation and Deep Linking

The app already supports navigation params through `ui_electron/src/contexts/NavigationContext.tsx`.

After the new `Customers` page is introduced:

- Update customer links to navigate to `customers` instead of `orders`
- Preserve deep-linking into a specific customer profile using `customerId`
- Keep direct navigation from analytics tables and order/customer rows

## Phase 5: Cleanup After Migration

Once the `Customers` page is fully functional:

- Remove or reduce the customer tab inside `Orders`
- Remove or reduce the customer tab inside `Insights`
- Keep a single canonical customer experience in the left nav

## Suggested Delivery Order

1. Add the new left-nav `Customers` button and placeholder page
2. Move profile search and customer overview into `Customers`
3. Move customer analytics into `Customers`
4. Add address field support to profile API and UI
5. Clean up duplicated customer entry points in `Orders` and `Insights`

## Initial Files Likely To Change

- `ui_electron/src/App.tsx`
- `ui_electron/src/pages/Customers.tsx`
- `ui_electron/src/pages/Orders.tsx`
- `ui_electron/src/pages/Insights.tsx`
- `ui_electron/src/components/CustomerLink.tsx`
- `ui_electron/src/components/CustomerProfile.tsx`
- `src/api/models.py`
- `src/api/routers/orders.py`
- `src/core/queries/customer_queries.py`

## Immediate Next Step

Implement Phase 1 only:

- Add the `Customers` nav button above `Orders`
- Create the placeholder `Customers` page
- Leave deeper customer consolidation for the next pass
