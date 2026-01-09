# PetPooja Order Schema Analysis

**Total Orders Analyzed:** 100

## Order Types Distribution

- Delivery: 68
- Dine In: 26
- Pick Up: 6

## Order Sources Distribution

- Zomato: 46
- POS: 32
- Swiggy: 19
- Home Website: 3

## Order Fields

| Field | Type(s) | Description |
|-------|---------|-------------|
| `assignee` | str | |
| `biller` | str | |
| `comment` | str | |
| `core_total` | float | |
| `created_on` | str | |
| `customer_invoice_id` | str | |
| `delivery_charges` | float | |
| `discount_total` | float | |
| `no_of_persons` | int | |
| `orderID` | int | |
| `order_from` | str | |
| `order_from_id` | str | |
| `order_type` | str | |
| `packaging_charge` | float | |
| `payment_type` | str | |
| `round_off` | str | |
| `service_charge` | int | |
| `status` | str | |
| `sub_order_type` | str | |
| `table_no` | str | |
| `tax_total` | float | |
| `token_no` | str | |
| `total` | float | |

## Customer Fields

| Field | Type(s) | Description |
|-------|---------|-------------|
| `address` | str | |
| `gstin` | str | |
| `name` | str | |
| `phone` | str | |

## Order Item Fields

| Field | Type(s) | Description |
|-------|---------|-------------|
| `addon` | list | |
| `category_name` | str | |
| `discount` | float | |
| `itemcode` | str | |
| `itemid` | int | |
| `name` | str | |
| `price` | float | |
| `quantity` | int | |
| `sap_code` | str | |
| `specialnotes` | str | |
| `tax` | float | |
| `total` | float | |
| `vendoritemcode` | str | |

## Addon Fields

| Field | Type(s) | Description |
|-------|---------|-------------|
| `addon_sap_code` | str | |
| `addonid` | str | |
| `group_name` | str | |
| `name` | str | |
| `price` | str, int | |
| `quantity` | str, int | |

## Tax Fields

| Field | Type(s) | Description |
|-------|---------|-------------|
| `amount` | float | |
| `rate` | float | |
| `title` | str | |
| `type` | str | |

## Discount Fields

| Field | Type(s) | Description |
|-------|---------|-------------|
| `amount` | float | |
| `rate` | float | |
| `title` | str | |
| `type` | str | |

## Sample Item Names (for menu matching)

- `Bean-to-bar 70% Dark Chocolate Ice Cream (Perfect Plenty (300ml))`
- `Monkey Business Ice Cream (Perfect Plenty (300ml))`
- `Monkey Business Ice Cream (Regular Tub (300ml))`
- `Boston Cream Pie Dessert(2pcs)`
- `Eggless Chocolate Ice Cream (Regular Tub (300ml))`
- `Boston Cream Pie Dessert(2pcs)`
- `Classic Chocolate Lamington (2pcs)`
- `Eggless Banoffee Ice Cream (Perfect Plenty (300ml))`
- `Bean-to-bar Dark Chocolate Ice Cream (Regular Tub (300ml))`
- `Classic Tiramisu`

## Sample Addon Names

- `Eggless Strawberry Cream Cheese Ice Cream 200ml`
- `Bean-to-bar 70% Dark Chocolate Ice Cream 200ml`
- `Cup`
- `Cup`
- `Cup`
- `Cup`
- `Cup`
- `Eggless Paan & Gulkand Ice Cream 200ml`
- `Coffee Mascarpone Ice Cream 200ml`
- `Eggless Strawberry Cream Cheese Ice Cream 200ml`
