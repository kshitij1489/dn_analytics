import unittest

from src.core.queries.customer_similarity_scoring import compute_name_similarity


class CustomerSimilarityScoringTests(unittest.TestCase):
    def test_multi_part_name_near_match_scores_above_short_single_token_exact_match(self) -> None:
        multi_part_score = compute_name_similarity("john smith", "john smit")
        single_token_score = compute_name_similarity("john", "john")

        self.assertGreater(multi_part_score, single_token_score)

    def test_middle_name_difference_scores_above_partial_name_only_match(self) -> None:
        full_name_score = compute_name_similarity("john a smith", "john smith")
        partial_name_score = compute_name_similarity("john smith", "john")

        self.assertGreater(full_name_score, partial_name_score)

    def test_longer_exact_name_match_gets_more_credit_than_shorter_exact_match(self) -> None:
        short_exact_score = compute_name_similarity("nazia", "nazia")
        long_exact_score = compute_name_similarity("priscillishwear", "priscillishwear")

        self.assertGreater(long_exact_score, short_exact_score)


if __name__ == "__main__":
    unittest.main()
