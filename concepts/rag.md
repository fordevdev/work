# RAG (Retrieval-Augmented Generation)

LLM에게 질문을 그대로 던지는 대신, 질의 시점에 관련 문서를 먼저 **검색(Retrieval)**해서 그 내용을 프롬프트에 함께 넣어준 뒤 답을 **생성(Generation)**하게 하는 패턴이다.

## 왜 필요한가

LLM은 학습 시점 이후의 정보나 사내 문서 같은 비공개 지식을 모른다. Fine-tuning으로 매번 학습시키는 건 비용도 크고 최신성 유지도 어렵다. RAG는 모델 자체는 그대로 두고, 답변 시점에 필요한 지식만 "주입"하는 방식이라 최신 데이터 반영이 훨씬 빠르고 싸다.

## 이 프로젝트에서의 역할

- **Retrieval** — Knowledge Store + Ranking Service가 담당 (벡터 유사도 검색 + graph 확장 + recency/authority/feedback 가중치)
- **Generation** — LLM(Claude)이 검색된 문서를 근거로 답변 생성, 출처 인용까지 포함

일반적인 RAG와 다른 점은, 검색 결과를 단순 유사도가 아니라 **최신성 × 출처신뢰도 × 피드백**으로 재정렬한다는 것과, 복잡한 질의에는 한 번의 검색이 아니라 여러 번 반복하는 [multi-hop agentic retrieval](/notes/concepts/agentic-retrieval)을 쓴다는 점이다.

---
[← 구현 계획으로 돌아가기](/notes/mattermost-second-brain-plan)
