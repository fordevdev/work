# Knowledge Graph & `supersedes`

[Vector embedding](/notes/concepts/vector-embeddings) 유사도만으로는 문서 "간의 관계"를 표현할 수 없다. 예를 들어 2024년에 작성된 설계 문서를 2026년 문서가 대체했다는 사실은, 두 문서가 내용적으로 비슷하다는 것(유사도가 높다는 것)과는 완전히 다른 정보다. 오히려 유사도만 보면 오래된 문서가 계속 상위에 노출될 수 있다.

## Graph로 표현하는 것

문서를 노드(node)로, 문서 간 관계를 엣지(edge)로 표현한다. 이 프로젝트에서 쓰는 핵심 엣지 타입:

- **`supersedes`** — 이 문서가 저 문서를 대체함 (Ranking 단계에서 대체된 문서는 자동으로 하위 랭크되거나 검색 결과에서 제외됨)
- **`references`** — 이 문서가 저 문서를 참조/링크함 (관련 문서를 함께 찾아주는 graph expansion에 사용)

## 왜 필요한가

- **충돌 해소**: 같은 주제에 대해 서로 다른 답을 주는 문서 두 개가 검색되면, `supersedes` 관계로 어느 쪽이 최신 정답인지 명확히 알 수 있다.
- **Multi-hop 탐색**: 질의와 직접 유사하지 않아도, 유사한 문서에 연결된 문서까지 넓혀서 찾을 수 있다 ([multi-hop agentic retrieval](/notes/concepts/agentic-retrieval)의 기반).

Graph 관계는 자동 추출이 어려운 경우가 많아, 초기에는 Confluence의 "이 페이지는 오래된 버전입니다" 같은 명시적 신호나 수동 태깅으로 시작하고, 점진적으로 자동화하는 게 현실적이다.

---
[← 구현 계획으로 돌아가기](/notes/mattermost-second-brain-plan)
