# MCP (Model Context Protocol)

Anthropic이 제안한 오픈 프로토콜로, LLM 애플리케이션이 외부 데이터 소스나 도구에 접근하는 방식을 표준화한다. USB-C 포트에 비유되곤 하는데, 각 서비스마다 다른 커스텀 연동을 만드는 대신 하나의 표준 인터페이스로 여러 클라이언트가 여러 서버에 붙을 수 있게 해준다.

## 왜 이 프로젝트에 쓰나

Mattermost bot 자체에 검색 로직을 하드코딩하면, 나중에 다른 클라이언트(예: Slack bot, CLI 도구, Claude Desktop)에서 같은 지식을 쓰고 싶을 때 매번 새로 연동해야 한다. 지식 계층을 **MCP 서버**로 노출하면:

- Mattermost bot은 MCP client로 `search_knowledge`, `get_related`, `submit_feedback` 같은 tool을 표준 방식으로 호출
- 나중에 다른 클라이언트가 생겨도 같은 MCP 서버를 그대로 재사용
- 로컬 개발 중에는 Claude Desktop/Code에 이 MCP 서버를 붙여서 봇 없이도 바로 테스트 가능

## Tool 스펙 (이 프로젝트 기준)

| Tool | 역할 |
|---|---|
| `search_knowledge(query, filters)` | 하이브리드 검색 + ranking 수행, 인용 가능한 청크 반환 |
| `get_related(doc_id)` | graph 상에서 연결된 문서 조회 |
| `get_document(doc_id)` | 특정 문서 원문 조회 |
| `submit_feedback(response_id, rating, chunk_ids)` | 사용자 피드백을 [Bayesian smoothing](/notes/concepts/bayesian-smoothing) 가중치 갱신에 반영 |

---
[← 구현 계획으로 돌아가기](/notes/mattermost-second-brain-plan)
