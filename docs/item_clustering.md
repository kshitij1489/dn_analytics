# Item Clustering Logic

This document explains how the system takes messy, free-text order items (e.g., "Eggless Choco Brwn") and turns them into clean, structured Menu Items (e.g., "Chocolate Brownie" | Type: "Dessert" | Variant: "Eggless").

## The Problem
Order data comes from webhooks with inconsistent names. A single product might appear as:
- "Dbl Choco Chip Cookie"
- "Double Chocolate Chip Cookie (1pc)"
- "Cookie - Double Choco"

We need to map all these to a single **Menu Item ID** to track total sales accurately.

## The Solution: "Cluster & Verify"

The system uses a 2-step process to handle incoming data.

### 1. The "Brain" (Persistent Knowledge)
The core of the system is the **Menu Item Registry** (stored in the database and backed up to JSON).
- **UUIDs**: Every unique Menu Item gets a deterministic UUID based on its Name + Type. 
- **Variants**: Sizes and flavors (e.g., "Small", "Eggless") are extracted and stored as `variants`.

### 2. Nested Clustering Hierarchy
The clustering is designed as a **Parent-Child relationship**:

*   **Parent Cluster (The Item)**: Represents the core products (e.g., "Cappuccino", "Brownie").
    *   This is the high-level bucket for analytics.
    *   Mapped via `menu_item_id`.

*   **Child Cluster (The Variant)**: Represents specific attributes nested under the parent.
    *   Examples: "Regular", "Large", "Eggless", "with Ice Cream".
    *   Mapped via `variant_id`.

**Example:**
> **Parent**: "Chocolate Brownie"
> *   *Child*: "Eggless"
> *   *Child*: "With Ice Cream"
> *   *Child*: "Box of 4"

This allows us to track that we sold **50 Brownies** total (Parent level), while identifying that **30 were Eggless** (Child level).

### 3. The Clustering Algorithm
When a new order arrives, the `CleaningService` performs the following steps:

1.  **Exact Match**:
    - Does this exact string `name + order_item_id` already exist in our mappings? 
    - If **YES** -> Use the existing Menu Item ID.

2.  **String Normalization**:
    - If **NO**, the system "cleans" the string (removes generic terms like "1pc", "Box", special chars).
    - It generates a tentative `clean_name`.

3.  **Fuzzy Prediction**:
    - The system looks for existing verified items that look similar to this new `clean_name` (using `difflib` string similarity).
    - If a match is found (Confidence > 0.7), it marks the new item as a **"Suggestion"**.

4.  **Auto-Creation (Unverified)**:
    - If no match is found, a NEW Menu Item is created.
    - **Status**: `is_verified = FALSE`
    - **Action Required**: These show up in the "Menu" dashboard under "Unclustered Items" for you to review.

## User Workflow
1.  **Incoming Orders**: Automatically mapped or created as "Unverified".
2.  **Review**: You go to the **Menu Page**.
3.  **Merge/Verify**:
    - If the system guessed right (e.g., it suggested "Choco Brownie" for "Choco Brwn"), you click **Merge**.
    - If it's a brand new item, you click **Verify**.
4.  **Learning**: Once verified, that mapping is permanent. Future orders with that name will automatically map correctly.

## Testing and Evaluation
To evaluate the current clustering results and inspect historical merge decisions, use the reproducible export script at [scripts/export_cluster_review.py](../scripts/export_cluster_review.py). It generates temporary review files under [tmp/cluster_review](../tmp/cluster_review).

### Generated Files
- [current_clusters.md](../tmp/cluster_review/current_clusters.md): Human-readable list grouped by `parent cluster :: child cluster`, with source item IDs and raw names.
- [current_clusters.csv](../tmp/cluster_review/current_clusters.csv): One row per current parent+child cluster summary.
- [current_cluster_members.csv](../tmp/cluster_review/current_cluster_members.csv): One row per current mapping/source ID.
- [merge_history.md](../tmp/cluster_review/merge_history.md): Human-readable merge history list with source name, target name, affected source IDs, raw names, and variant assignments.
- [merge_history.csv](../tmp/cluster_review/merge_history.csv): One row per merge event.

### Current Output Sizes
- 232 current parent+child cluster rows
- 468 current source mapping rows
- 41 merge-history events

### Important Nuance
Some mappings are keyed by generated IDs rather than observed `petpooja_itemid` / `petpooja_addonid`, so those rows show `unknown` or blank source-kind/raw-name context in the export.
- In the current export, this affects 74 current mapping rows.
- In the current export, this affects 28 merge-history rows.

### Regenerate the Review Files
```bash
python3 scripts/export_cluster_review.py
```
