# Mattermost 사내 Second Brain — 요구사항 정리 및 구현 계획

사내 문의가 주로 JIRA 티켓, Confluence 페이지, 그리고 Mattermost 채널에 쌓인 과거 답변에 의존하고 있다. 이 지식을 하나로 모아 Mattermost bot이 직접 답하게 만들면 반복 문의 비용을 크게 줄일 수 있을 것 같아 정리한다. **Bot server 자체는 이미 존재**하므로, 이번 작업의 범위는 그 bot이 참조할 "Knowledge Layer"를 새로 만들고 bot에 연동하는 것이다.

## 1. 요구사항

- JIRA / Confluence / Mattermost 채널 히스토리를 하나의 지식 소스로 통합한다.
- 답변 시 **최신 데이터·최근 피드백에 가중치**를 둔다 — 오래된 정보가 최신 정보를 덮어쓰지 않게 한다.
- 복잡한(여러 소스를 엮어야 하는) 요청도 처리할 수 있어야 한다.
- 사용자의 "도움이 됐는지" 피드백을 실제 검색 가중치에 반영한다.
- 기존 Mattermost bot server를 재사용한다 — bot을 새로 만들지 않고, Knowledge Layer를 [MCP](/notes/concepts/mcp) tool로 노출해서 기존 bot에 연결한다.

## 2. 주요 고려사항

- **단순 time decay만으로는 부족하다.** 오래됐지만 여전히 맞는 Confluence 설계 문서가, 어제 캐주얼하게 던진 Mattermost 잡담보다 낮은 score를 받으면 안 된다 → recency와 별개로 **authority(출처 신뢰도)** 축이 필요하다.
- **문서 충돌**은 [knowledge graph](/notes/concepts/knowledge-graph)의 `supersedes`(대체) relation으로 해소한다 — [embedding](/notes/concepts/vector-embeddings) 유사도만으로는 "이 문서가 저 문서를 대체했다"는 사실을 표현할 수 없다.
- **복잡한 질의**는 단발 [RAG](/notes/concepts/rag)로는 부족할 가능성이 크다 — [multi-hop agentic retrieval](/notes/concepts/agentic-retrieval)이 필요한데, 이는 응답 latency·비용 증가라는 트레이드오프를 동반한다.
- **권한**: JIRA/Confluence의 비공개 space를 bot이 그대로 노출하면 안 된다. 전사 크롤링 대신 whitelist space/project부터 시작한다.
- **피드백 attribution 문제**: 답변 하나가 보통 여러 chunk를 조합해서 나오기 때문에, reaction 하나만으로 어떤 chunk가 잘했는지 특정하기 어렵다 → [Bayesian smoothing](/notes/concepts/bayesian-smoothing) + time decay + user-diversity correction이 필요하다.
- 사내 bot 특성상 피드백 절대량이 적어서, 소수의 목소리 큰 유저에게 가중치가 쏠릴 위험이 있다.

## 3. 아키텍처

```
[JIRA/Confluence/Mattermost 이력]
        │
        ▼
  Ingestion Layer  ──▶  Knowledge Store (Vector + Graph)
        │                        │
        │                        ▼
        │                Ranking Service (sim × recency × authority × feedback)
        │                        │
        │                        ▼
        │                  MCP Server (tools)
        │                        │
        │                        ▼
        └───────────────▶  기존 Mattermost bot server ──▶ 사용자
                                  │
                    (reaction) ◀──┘
                                  │
                                  ▼
                     Feedback attribution·가중치 갱신 ──▶ Knowledge Store로 반영
```

신규로 만들어야 하는 것은 **Ingestion, Knowledge Store, Ranking Service, MCP Server, Feedback 모듈** 다섯 가지이고, Mattermost 쪽은 **기존 bot server에 MCP client를 붙이는 확장 작업**이다. 전체 다이어그램은 [HLD 페이지](/notes/hld) 참고.

## 4. 컴포넌트별 구성

### A. Ingestion & Sync Pipeline — `effort: high`

API 3종의 인증·페이지네이션·rate limit이 제각각이고, 증분 동기화에서 누락·중복을 막는 로직이 실제로 가장 오래 걸리는 지점.

- JIRA REST API connector (이슈/댓글/상태변경 이력 수집)
- Confluence REST API connector (페이지 + 버전 이력, `version.when`/`version.number` 추적)
- Mattermost 채널 히스토리 backfill (기존 bot server가 이미 갖고 있는 API token/권한 재사용 검토)
- Change detection: webhook 우선, 미지원 구간은 polling + diff
- Chunker — 문서 구조(제목/코드블록) 보존한 chunking
- Embedding batch job (scheduler)
- Metadata tagging — 출처 타입/작성자/생성·수정일/권한 scope
- Permission whitelist 설계 (수집 대상 space/project 목록)

### B. Knowledge Store — `effort: medium`

- Vector DB provisioning ([pgvector](/notes/concepts/vector-embeddings) 등)
- Schema 설계: `documents`, `chunks`, `edges(references/supersedes)`, `feedback_scores`
- Upsert API — 중복 방지 키(`source_type + source_id + chunk_index`)

### C. Hybrid Ranking Service — `effort: high`

네 가지 신호의 가중치 튜닝과, "오래됐지만 맞는 문서"를 억울하게 밀어내지 않는 균형이 핵심 난제.

```
score = sim(q, d) × time_decay(d) × authority(d) × feedback(d)
```

- Scoring 함수 구현 (가중치는 설정값으로 분리해서 튜닝 가능하게)
- Graph expansion 검색 (N-hop 관련 문서 탐색)
- `supersedes` 자동 감지 → 대체된 문서 하위 rank/제외
- 복잡 질의용 [multi-hop agentic retrieval](/notes/concepts/agentic-retrieval) 프로토타입

### D. MCP Server — `effort: medium`

- [MCP](/notes/concepts/mcp) server scaffolding
- Tool spec: `search_knowledge`, `get_related`, `get_document`, `submit_feedback`
- Ranking Service 연동
- Claude Desktop/Code로 로컬 연결 검증

### E. Mattermost Bot Integration — `effort: low-medium` (bot server는 기존 것 재사용)

- 기존 bot server에 MCP client library 추가
- LLM 호출 시 MCP tool(`search_knowledge` 등) attach
- 답변에 출처 링크(JIRA 티켓 / Confluence 페이지 / 원본 스레드) citation 포맷팅
- 답변 메시지에 reaction(👍/👎) 유도
- Reaction event 구독 → `submit_feedback` MCP 호출 연동
- 기존 메시지 핸들러와 신규 지식질의 핸들러 routing 분리 (기존 기능 회귀 방지)

### F. Feedback Attribution Module — `effort: medium-high`

- 답변 생성 시 실제 인용된 chunk ID 기록 (attention mapping)
- [Bayesian smoothing](/notes/concepts/bayesian-smoothing) score batch 계산: `(👍 + 1) / (전체 + 2)`
- Time decay 적용
- 동일 유저 반복 반응 discount (user-diversity correction)

### G. Observability/Admin — `effort: low`

- 자주 인용/무시되는 문서 dashboard
- 오래된 문서 업데이트 알림

## 5. 구현 계획

**2026-07-13(다음 주 월요일)부터 2026-09-04까지 8주** 계획. Phase 1(W1–3)은 최소 기능으로 가치를 빠르게 검증하고, Phase 2(W4–6)에서 graph·권한을, Phase 3(W7–8)에서 feedback loop을 채운다.

<div class="gantt-wrap">
<table class="gantt">
<thead>
<tr><th></th><th>W1</th><th>W2</th><th>W3</th><th>W4</th><th>W5</th><th>W6</th><th>W7</th><th>W8</th></tr>
</thead>
<tbody>
<tr><th>Ingestion (A)</th><td class="bar c-a"></td><td class="bar c-a"></td><td></td><td class="bar c-a"></td><td></td><td></td><td></td><td></td></tr>
<tr><th>Knowledge Store (B)</th><td></td><td class="bar c-b"></td><td class="bar c-b"></td><td class="bar c-b"></td><td></td><td></td><td></td><td></td></tr>
<tr><th>Ranking Service (C)</th><td></td><td></td><td class="bar c-c"></td><td></td><td class="bar c-c"></td><td class="bar c-c"></td><td class="bar c-c"></td><td class="bar c-c"></td></tr>
<tr><th>MCP Server (D)</th><td></td><td></td><td class="bar c-d"></td><td class="bar c-d"></td><td></td><td></td><td class="bar c-d"></td><td></td></tr>
<tr><th>Bot Integration (E)</th><td></td><td></td><td class="bar c-e"></td><td></td><td class="bar c-e"></td><td></td><td class="bar c-e"></td><td></td></tr>
<tr><th>Feedback Module (F)</th><td></td><td></td><td></td><td></td><td></td><td></td><td class="bar c-f"></td><td class="bar c-f"></td></tr>
<tr><th>Observability (G)</th><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td class="bar c-g"></td></tr>
</tbody>
</table>
</div>
<p style="font-size:0.78rem;color:var(--school-text-dim);margin-bottom:14px;">굵은 세로선 = phase 경계 (Phase 1: W1–3 · Phase 2: W4–6 · Phase 3: W7–8)</p>

### W1 (07/13 ~ 07/17) — Ingestion 기초
- JIRA connector (auth, issue+comment pull)
- Confluence connector (page+version history pull)
- Chunking 로직 초안 (문서 구조 보존)

### W2 (07/20 ~ 07/24) — Knowledge Store 구축
- Vector DB provisioning (pgvector)
- Schema 설계: `documents`/`chunks`
- Embedding batch job + upsert API 연동
- Ingestion → Knowledge Store end-to-end 연결

### W3 (07/27 ~ 07/31) — 최소 기능 검색 + Bot 연동 (Phase 1 완료)
- Ranking: `sim × recency`만 반영한 기본 scoring
- MCP server scaffolding + `search_knowledge` tool
- 기존 Mattermost bot에 MCP client 연결 (읽기 전용 질의)
- Phase 1 데모/검증

### W4 (08/03 ~ 08/07) — Mattermost 이력 + Graph 스키마
- Mattermost 채널 히스토리 backfill connector
- Graph edges 스키마 (`references`/`supersedes`)
- Authority scoring 설계 (출처 타입별 가중치)

### W5 (08/10 ~ 08/14) — Graph 확장 + Citation
- Ranking에 graph expansion 추가 (N-hop 관련 문서)
- `supersedes` 자동 충돌 해소
- Bot 답변에 출처 citation 포맷팅

### W6 (08/17 ~ 08/21) — 권한/Whitelist + Phase 2 검증
- 수집 대상 space whitelist 적용
- End-to-end 통합 테스트, 버그 수정 buffer
- Phase 2 데모/검증

### W7 (08/24 ~ 08/28) — Feedback Loop
- Bot 답변에 reaction(👍/👎) 유도 + event 구독
- Attention mapping (답변에 인용된 chunk 기록)
- `submit_feedback` MCP tool 구현
- Bayesian smoothing 계산 로직

### W8 (08/31 ~ 09/04) — 마무리 (Phase 3 완료)
- Time decay + 주기적 재계산 batch
- [Multi-hop agentic retrieval](/notes/concepts/agentic-retrieval) 프로토타입 (복잡 질의용)
- Observability dashboard (자주 인용/무시되는 문서) — stretch
- 전체 rollout 준비 및 최종 검증

## 참고

전체 시스템 다이어그램은 [HLD 페이지](/notes/hld)로 따로 정리해뒀다.
