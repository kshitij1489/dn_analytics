"""
Item Matching Logic

This module provides functions to match raw order item names from PetPooja
to normalized menu items and variants in the database.

The matching process:
1. Clean the raw name using clean_order_item.py
2. Query database for exact match
3. If no exact match, try fuzzy matching
4. Return match result with confidence score
"""

from typing import Dict, Optional, Tuple, List
from data_cleaning.clean_order_item import clean_order_item_name


class ItemMatcher:
    """
    Matches order items to menu items and variants in the database.
    
    Usage:
        matcher = ItemMatcher(db_connection)
        result = matcher.match_item("Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))")
        # Returns: {
        #     'menu_item_id': 45,
        #     'variant_id': 8,
        #     'match_confidence': 100,
        #     'match_method': 'exact'
        # }
    """
    
    def __init__(self, db_connection):
        """
        Initialize matcher with database connection.
        
        Args:
            db_connection: Database connection object (e.g., psycopg2, sqlite3, SQLAlchemy)
        """
        self.db = db_connection
        self._menu_item_cache = {}  # Cache for menu items (name, type) -> menu_item_id
        self._variant_cache = {}    # Cache for variants variant_name -> variant_id
    
    def _get_menu_item_id(self, name: str, type: str) -> Optional[int]:
        """
        Get menu_item_id for a given name and type.
        Uses cache for performance.
        
        Args:
            name: Menu item name
            type: Menu item type (Ice Cream, Dessert, etc.)
        
        Returns:
            menu_item_id or None if not found
        """
        cache_key = (name.lower(), type.lower())
        if cache_key in self._menu_item_cache:
            return self._menu_item_cache[cache_key]
        
        # Query database
        # Adapt this query to your database library
        cursor = self.db.cursor()
        cursor.execute("""
            SELECT menu_item_id 
            FROM menu_items 
            WHERE LOWER(name) = LOWER(%s) AND LOWER(type) = LOWER(%s)
            LIMIT 1
        """, (name, type))
        
        result = cursor.fetchone()
        menu_item_id = result[0] if result else None
        
        # Cache result
        self._menu_item_cache[cache_key] = menu_item_id
        return menu_item_id
    
    def _get_variant_id(self, variant_name: str) -> Optional[int]:
        """
        Get variant_id for a given variant name.
        Uses cache for performance.
        
        Args:
            variant_name: Variant name (e.g., "MINI_TUB_160GMS")
        
        Returns:
            variant_id or None if not found
        """
        cache_key = variant_name.upper()
        if cache_key in self._variant_cache:
            return self._variant_cache[cache_key]
        
        # Query database
        cursor = self.db.cursor()
        cursor.execute("""
            SELECT variant_id 
            FROM variants 
            WHERE UPPER(variant_name) = UPPER(%s)
            LIMIT 1
        """, (variant_name,))
        
        result = cursor.fetchone()
        variant_id = result[0] if result else None
        
        # Cache result
        self._variant_cache[cache_key] = variant_id
        return variant_id
    
    def _calculate_similarity(self, name1: str, name2: str) -> float:
        """
        Calculate similarity score between two names (0-100).
        Uses multiple strategies for better matching.
        """
        name1_lower = name1.lower().strip()
        name2_lower = name2.lower().strip()
        
        # Exact match
        if name1_lower == name2_lower:
            return 100.0
        
        # One contains the other (high confidence)
        if name1_lower in name2_lower:
            # Calculate score based on how much of the shorter name is in the longer
            return min(95.0, (len(name1_lower) / len(name2_lower)) * 100)
        if name2_lower in name1_lower:
            return min(95.0, (len(name2_lower) / len(name1_lower)) * 100)
        
        # Word-based similarity
        words1 = set(name1_lower.split())
        words2 = set(name2_lower.split())
        
        # Remove common words that don't matter for matching
        stop_words = {'ice', 'cream', 'the', 'a', 'an', 'and', 'or', 'with', '&'}
        words1 = words1 - stop_words
        words2 = words2 - stop_words
        
        if not words1 or not words2:
            return 0.0
        
        # Jaccard similarity (intersection over union)
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        jaccard_score = (intersection / union) * 100 if union > 0 else 0
        
        # Word order similarity (check if words appear in similar order)
        words1_list = [w for w in name1_lower.split() if w not in stop_words]
        words2_list = [w for w in name2_lower.split() if w not in stop_words]
        
        # Check if significant words match
        significant_words1 = {w for w in words1_list if len(w) > 3}
        significant_words2 = {w for w in words2_list if len(w) > 3}
        
        if significant_words1 and significant_words2:
            significant_match = len(significant_words1 & significant_words2) / len(significant_words1 | significant_words2)
            significant_score = significant_match * 100
        else:
            significant_score = 0
        
        # Combined score (weighted)
        final_score = max(jaccard_score, significant_score * 0.8)
        
        # Boost score if one name is a prefix/suffix of the other
        if name1_lower.startswith(name2_lower[:10]) or name2_lower.startswith(name1_lower[:10]):
            final_score = min(95.0, final_score * 1.2)
        
        return final_score
    
    def _fuzzy_match_menu_item(self, name: str, type: str, threshold: int = 75) -> Optional[int]:
        """
        Fuzzy match menu item using improved similarity scoring.
        Prefers matches that preserve important prefixes like "Eggless".
        
        Args:
            name: Menu item name to match
            type: Menu item type
            threshold: Minimum similarity score (0-100)
        
        Returns:
            menu_item_id or None if no good match found
        """
        cursor = self.db.cursor()
        cursor.execute("""
            SELECT menu_item_id, name
            FROM menu_items 
            WHERE LOWER(type) = LOWER(%s)
        """, (type,))
        
        candidates = cursor.fetchall()
        
        best_match = None
        best_score = 0
        
        # Check if name has important prefixes
        name_lower = name.lower()
        has_eggless = 'eggless' in name_lower or 'eggles' in name_lower
        
        for menu_item_id, menu_name in candidates:
            score = self._calculate_similarity(name, menu_name)
            
            if score == 100.0:
                # Exact match found
                return menu_item_id
            
            # Boost score if important prefixes match
            menu_name_lower = menu_name.lower()
            menu_has_eggless = 'eggless' in menu_name_lower
            
            # Prefer matches where "Eggless" status matches
            if has_eggless == menu_has_eggless:
                score = score * 1.1  # Boost by 10% if prefix matches
            elif has_eggless and not menu_has_eggless:
                score = score * 0.7  # Penalize if we have "Eggless" but menu item doesn't
            elif not has_eggless and menu_has_eggless:
                score = score * 0.8  # Slight penalty if menu has "Eggless" but we don't
            
            # Cap score at 100
            score = min(100.0, score)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = menu_item_id
        
        return best_match
    
    def _fuzzy_match_variant(self, cleaned_variant: str, raw_name: str, menu_item_id: int) -> Optional[int]:
        """
        Try to find a variant that matches the cleaned variant or raw name.
        Looks for size-based matches (e.g., "200ml" -> "MINI_TUB_200ML").
        
        Args:
            cleaned_variant: The cleaned variant name (e.g., "MINI_TUB_200ML")
            raw_name: The original raw item name (for extracting size info)
            menu_item_id: The matched menu item ID
        
        Returns:
            variant_id or None if no match found
        """
        # First, check if we can extract size information from the raw name
        import re
        
        raw_lower = raw_name.lower()
        
        # Extract size patterns from raw name
        size_patterns = {
            '200ml': ['MINI_TUB_200ML', 'PERFECT_PLENTY_200GMS'],
            '200gm': ['PERFECT_PLENTY_200GMS', 'MINI_TUB_200ML'],
            '300ml': ['REGULAR_TUB_300ML', 'PERFECT_PLENTY_300ML'],
            '220gm': ['REGULAR_TUB_220GMS'],
            '160gm': ['MINI_TUB_160GMS'],
            '120gm': ['REGULAR_SCOOP_120GMS'],
            '60gm': ['JUNIOR_SCOOP_60GMS'],
            '500gm': ['FAMILY_TUB_500GMS'],
            '2pc': ['2_PIECES'],
            '1pc': ['1_PIECE'],
        }
        
        # Try to match based on size in raw name
        for size_key, variant_candidates in size_patterns.items():
            if size_key in raw_lower:
                # Try each candidate variant
                for variant_name in variant_candidates:
                    variant_id = self._get_variant_id(variant_name)
                    if variant_id:
                        # Check if this variant exists for this menu item
                        cursor = self.db.cursor()
                        cursor.execute("""
                            SELECT menu_item_variant_id
                            FROM menu_item_variants
                            WHERE menu_item_id = %s AND variant_id = %s
                        """, (menu_item_id, variant_id))
                        if cursor.fetchone():
                            cursor.close()
                            return variant_id
                        cursor.close()
        
        # If no size-based match, try to find variants that contain similar keywords
        # Extract keywords from cleaned_variant (e.g., "MINI_TUB_200ML" -> ["mini", "tub", "200", "ml"])
        variant_keywords = re.findall(r'\d+|[a-z]+', cleaned_variant.lower())
        
        cursor = self.db.cursor()
        cursor.execute("""
            SELECT v.variant_id, v.variant_name
            FROM variants v
            JOIN menu_item_variants miv ON v.variant_id = miv.variant_id
            WHERE miv.menu_item_id = %s
        """, (menu_item_id,))
        
        available_variants = cursor.fetchall()
        cursor.close()
        
        if not available_variants:
            return None
        
        # Score each available variant based on keyword overlap
        best_variant_id = None
        best_score = 0
        
        for variant_id, variant_name in available_variants:
            variant_name_keywords = re.findall(r'\d+|[a-z]+', variant_name.lower())
            
            # Calculate overlap
            overlap = len(set(variant_keywords) & set(variant_name_keywords))
            total_keywords = len(set(variant_keywords) | set(variant_name_keywords))
            
            if total_keywords > 0:
                score = (overlap / total_keywords) * 100
                if score > best_score and score >= 50:  # At least 50% keyword overlap
                    best_score = score
                    best_variant_id = variant_id
        
        return best_variant_id
    
    def _lookup_parsing(self, raw_name: str) -> Optional[Dict]:
        """
        Check item_parsing_table for existing mapping.
        """
        try:
            cursor = self.db.cursor()
            cursor.execute("""
                SELECT cleaned_name, type, variant, is_verified 
                FROM item_parsing_table 
                WHERE raw_name = %s
            """, (raw_name,))
            result = cursor.fetchone()
            cursor.close()
            
            if result:
                return {
                    'cleaned_name': result[0],
                    'type': result[1],
                    'variant': result[2],
                    'is_verified': result[3]
                }
        except Exception:
            # Table might not exist yet or connection error
            if 'cursor' in locals():
                cursor.close()
        return None

    def _save_suggestion(self, raw_name: str, suggestion: Dict):
        """
        Save a new unverified suggestion to item_parsing_table.
        """
        try:
            cursor = self.db.cursor()
            cursor.execute("""
                INSERT INTO item_parsing_table (raw_name, cleaned_name, type, variant, is_verified)
                VALUES (%s, %s, %s, %s, FALSE)
                ON CONFLICT (raw_name) DO NOTHING
            """, (raw_name, suggestion['name'], suggestion['type'], suggestion['variant']))
            self.db.commit()
            cursor.close()
        except Exception:
            self.db.rollback()
            if 'cursor' in locals():
                cursor.close()

    def match_item(self, raw_name: str, 
                   use_fuzzy: bool = True,
                   fuzzy_threshold: int = 80) -> Dict[str, Optional[int]]:
        """
        Match a raw order item name to menu_item_id and variant_id.
        
        Args:
            raw_name: Raw item name from PetPooja order
            use_fuzzy: Whether to use fuzzy matching if exact match fails
            fuzzy_threshold: Minimum similarity score for fuzzy matching (0-100)
        
        Returns:
            Dictionary with:
                - menu_item_id: int or None
                - variant_id: int or None
                - match_confidence: int (0-100) or None
                - match_method: str ('parsing_table', 'suggestion', 'fuzzy', etc.)
        """
        # Step 0: Check Parsing Table (Version 2 Source of Truth)
        parsing_entry = self._lookup_parsing(raw_name)
        
        if parsing_entry:
            cleaned_name = parsing_entry['cleaned_name']
            cleaned_type = parsing_entry['type']
            cleaned_variant = parsing_entry['variant']
            is_verified = parsing_entry['is_verified']
            match_method = 'parsing_table'
            confidence_base = 100 if is_verified else 90
        else:
            # Step 1: Clean/Suggest using legacy logic (if miss)
            try:
                cleaned = clean_order_item_name(raw_name)
                
                # Save suggestion for future conflict resolution
                self._save_suggestion(raw_name, cleaned)
                
                cleaned_name = cleaned['name']
                cleaned_type = cleaned['type']
                cleaned_variant = cleaned['variant']
                match_method = 'suggestion'
                confidence_base = 80 # Unverified suggestion
            except Exception as e:
                return {
                    'menu_item_id': None,
                    'variant_id': None,
                    'match_confidence': None,
                    'match_method': None,
                    'error': str(e)
                }
        
        # Step 2: Try exact match for menu item using Cleaned Attributes
        menu_item_id = self._get_menu_item_id(cleaned_name, cleaned_type)
        
        match_confidence = confidence_base
        
        # Step 3: If no exact menu item match ...
        # If we came from 'parsing_table', we assume the Name is correct, so maybe it's just missing in `menu_items`.
        # But if we came from 'suggestion', maybe the regex failed?
        
        if menu_item_id is None and use_fuzzy:
            # Try fuzzy match on the CLEANED name
            menu_item_id = self._fuzzy_match_menu_item(cleaned_name, cleaned_type, fuzzy_threshold)
            if menu_item_id:
                match_method = 'fuzzy_cleaned'
                match_confidence = min(match_confidence, fuzzy_threshold)
        
        # Step 4: Get variant_id
        variant_id = self._get_variant_id(cleaned_variant)
        
        # If variant not found and we have a menu_item_id, try fuzzy variant matching
        if variant_id is None and menu_item_id is not None:
             # Try fuzzy/size matching using the original raw name or cleaned variant
             # Logic from original code handles this
             variant_id = self._fuzzy_match_variant(cleaned_variant, raw_name, menu_item_id)
             if variant_id:
                 match_confidence = max(70, match_confidence - 10)
        
        # Step 5: Adjust confidence if variant not found
        if menu_item_id and variant_id is None:
            match_confidence = (match_confidence or 100) * 0.7
            match_method = f"{match_method}_partial"
        elif menu_item_id is None:
            match_confidence = None
            match_method = None
        
        return {
            'menu_item_id': menu_item_id,
            'variant_id': variant_id,
            'match_confidence': match_confidence,
            'match_method': match_method,
            'cleaned_name': cleaned_name,
            'cleaned_type': cleaned_type,
            'cleaned_variant': cleaned_variant
        }
    
    def match_addon(self, raw_name: str, 
                   use_fuzzy: bool = True,
                   fuzzy_threshold: int = 80) -> Dict[str, Optional[int]]:
        """
        Match an addon name to menu_item_id and variant_id.
        Same logic as match_item, but optimized for addons.
        
        Args:
            raw_name: Raw addon name from PetPooja order
            use_fuzzy: Whether to use fuzzy matching if exact match fails
            fuzzy_threshold: Minimum similarity score for fuzzy matching (0-100)
        
        Returns:
            Same format as match_item()
        """
        # Addons are typically simpler names, so we can use the same matching logic
        return self.match_item(raw_name, use_fuzzy, fuzzy_threshold)
    
    def batch_match(self, raw_names: List[str],
                   use_fuzzy: bool = True,
                   fuzzy_threshold: int = 80) -> List[Dict[str, Optional[int]]]:
        """
        Match multiple items in batch (for performance).
        
        Args:
            raw_names: List of raw item names
            use_fuzzy: Whether to use fuzzy matching
            fuzzy_threshold: Minimum similarity score
        
        Returns:
            List of match results (same format as match_item())
        """
        results = []
        for raw_name in raw_names:
            result = self.match_item(raw_name, use_fuzzy, fuzzy_threshold)
            results.append(result)
        return results
    
    def clear_cache(self):
        """Clear the internal caches (useful for testing or memory management)"""
        self._menu_item_cache.clear()
        self._variant_cache.clear()


# ============================================================================
# Standalone Functions (for use without class)
# ============================================================================

def match_item_standalone(raw_name: str, db_connection,
                         use_fuzzy: bool = True,
                         fuzzy_threshold: int = 80) -> Dict[str, Optional[int]]:
    """
    Standalone function to match an item (creates matcher internally).
    
    Args:
        raw_name: Raw item name from PetPooja order
        db_connection: Database connection
        use_fuzzy: Whether to use fuzzy matching
        fuzzy_threshold: Minimum similarity score
    
    Returns:
        Match result dictionary
    """
    matcher = ItemMatcher(db_connection)
    return matcher.match_item(raw_name, use_fuzzy, fuzzy_threshold)


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    # Example usage (requires database connection)
    # import psycopg2
    # 
    # conn = psycopg2.connect(
    #     host="localhost",
    #     database="analytics",
    #     user="user",
    #     password="password"
    # )
    # 
    # matcher = ItemMatcher(conn)
    # 
    # # Match a single item
    # result = matcher.match_item("Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))")
    # print(result)
    # # Output: {
    # #     'menu_item_id': 45,
    # #     'variant_id': 8,
    # #     'match_confidence': 100,
    # #     'match_method': 'exact',
    # #     'cleaned_name': 'Old Fashion Vanilla Ice Cream',
    # #     'cleaned_type': 'Ice Cream',
    # #     'cleaned_variant': 'REGULAR_TUB_300ML'
    # # }
    # 
    # # Match an addon
    # addon_result = matcher.match_addon("Cup")
    # print(addon_result)
    # 
    # # Batch match
    # items = [
    #     "Banoffee Ice Cream (Mini Tub)",
    #     "Boston Cream Pie Dessert(2pcs)",
    #     "Waffle Cone"
    # ]
    # results = matcher.batch_match(items)
    # for item, result in zip(items, results):
    #     print(f"{item} -> menu_item_id={result['menu_item_id']}, variant_id={result['variant_id']}")
    
    print("ItemMatcher module loaded successfully.")
    print("To use, create an ItemMatcher instance with a database connection.")

