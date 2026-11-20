import unittest
from typing import Any
from unittest.mock import patch

from langchain_core.documents import Document

from app import knowledge


def make_document(text: str, source: str = "policy.md", page: int | None = None) -> Document:
    metadata: dict[str, Any] = {"source": source, "document_id": "doc-1"}
    if page is not None:
        metadata["page"] = page
    return Document(page_content=text, metadata=metadata)


class FakeVectorStore:
    """Minimal stand-in for Chroma that records the requested ``k``."""

    def __init__(self, results: list[tuple[Document, float]]) -> None:
        self.results = results
        self.requested_k: int | None = None

    def similarity_search_with_score(self, query: str, k: int) -> list[tuple[Document, float]]:
        self.requested_k = k
        return self.results[:k]


class RetrievalStrategyTests(unittest.TestCase):
    def test_hybrid_promotes_keyword_match_over_pure_vector_distance(self) -> None:
        literal = make_document("政策编号 REFUND-7788 适用于跨境订单。", source="policy.md")
        semantic = make_document("一段语义相近但没有出现单号的说明文字。", source="other.md")
        store = FakeVectorStore([(semantic, 0.05), (literal, 0.15)])

        with patch.object(knowledge, "vector_store", return_value=store):
            similarity = knowledge.retrieve_documents("kb", "REFUND-7788 适用哪些订单", k=2)
            hybrid = knowledge.retrieve_documents(
                "kb", "REFUND-7788 适用哪些订单", strategy="hybrid", k=2
            )

        self.assertEqual(similarity[0][0].metadata["source"], "other.md")
        self.assertEqual(hybrid[0][0].metadata["source"], "policy.md")
        self.assertGreater(store.requested_k or 0, 2)
        self.assertLessEqual(hybrid[0][1], 1.0)

    def test_similarity_scores_are_normalised_and_chunks_deduped(self) -> None:
        repeated = "退款期限为 30 天。"
        store = FakeVectorStore(
            [
                (make_document(repeated, page=1), 0.0),
                (make_document(repeated, page=1), 0.0),
                (make_document("其他段落", source="faq.md", page=2), 3.0),
            ]
        )

        with patch.object(knowledge, "vector_store", return_value=store):
            ranked = knowledge.retrieve_documents("kb", "退款期限", k=5)

        self.assertEqual(len(ranked), 2)
        self.assertAlmostEqual(ranked[0][1], 1.0)
        self.assertAlmostEqual(ranked[1][1], 0.25)

    def test_min_score_filters_weak_matches(self) -> None:
        store = FakeVectorStore(
            [
                (make_document("退款期限为 30 天。"), 0.0),
                (make_document("模糊相关的内容", source="faq.md"), 9.0),
            ]
        )

        with patch.object(knowledge, "vector_store", return_value=store):
            ranked = knowledge.retrieve_documents("kb", "退款期限", k=5, min_score=0.5)

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][0].metadata["source"], "policy.md")

    def test_k_is_clamped_to_supported_range(self) -> None:
        store = FakeVectorStore([(make_document(f"段落 {index}"), 0.0) for index in range(40)])

        with patch.object(knowledge, "vector_store", return_value=store):
            knowledge.retrieve_documents("kb", "问题", k=500)

        self.assertEqual(store.requested_k, knowledge.MAX_RETRIEVAL_K)

    def test_unknown_strategy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            knowledge.retrieve_documents("kb", "问题", strategy="lexical")

    def test_lexical_overlap_handles_chinese_bigrams(self) -> None:
        self.assertGreater(knowledge.lexical_overlap("退款期限", "退款期限为 30 天"), 0.9)
        self.assertEqual(knowledge.lexical_overlap("退款", "完全不同"), 0.0)
        self.assertEqual(knowledge.lexical_overlap("", "任意内容"), 0.0)

    def test_build_messages_keeps_history_between_system_and_question(self) -> None:
        messages = knowledge.build_messages(
            "第二问",
            ["[1] 退款期限为 30 天。"],
            history=[
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "第一答"},
                {"role": "tool", "content": "忽略我"},
            ],
        )

        self.assertEqual(
            [type(message).__name__ for message in messages],
            [
                "SystemMessage",
                "HumanMessage",
                "AIMessage",
                "HumanMessage",
            ],
        )
        self.assertIn("退款期限为 30 天", messages[0].content)
        self.assertEqual(messages[-1].content, "用户问题：第二问")


if __name__ == "__main__":
    unittest.main()
