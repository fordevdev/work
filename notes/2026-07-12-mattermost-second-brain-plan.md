# Mattermost 사내 Second Brain — 요구사항 정리 및 구현 계획

사내 문의가 주로 JIRA 티켓, Confluence 페이지, 그리고 Mattermost 채널에 쌓인 과거 답변에 의존하고 있다. 이 지식을 하나로 모아 Mattermost 봇이 직접 답하게 만들면 반복 문의 비용을 크게 줄일 수 있을 것 같아 정리한다. **봇서버 자체는 이미 존재**하므로, 이번 작업의 범위는 그 봇이 참조할 "지식 계층"을 새로 만들고 봇에 연동하는 것이다.

## 1. 요구사항

- JIRA / Confluence / Mattermost 채널 히스토리를 하나의 지식 소스로 통합한다.
- 답변 시 **최신 데이터·최근 피드백에 가중치**를 둔다 — 오래된 정보가 최신 정보를 덮어쓰지 않게 한다.
- 복잡한(여러 소스를 엮어야 하는) 요청도 처리할 수 있어야 한다.
- 사용자의 "도움이 됐는지" 피드백을 실제 검색 가중치에 반영한다.
- 기존 Mattermost 봇서버를 재사용한다 — 봇을 새로 만들지 않고, 지식 계층을 MCP 툴로 노출해서 기존 봇에 연결한다.

## 2. 주요 고려사항

- **단순 시간감쇠만으로는 부족하다.** 오래됐지만 여전히 맞는 Confluence 설계 문서가, 어제 캐주얼하게 던진 Mattermost 잡담보다 낮은 점수를 받으면 안 된다 → 최신성과 별개로 **출처 신뢰도** 축이 필요하다.
- **문서 충돌**은 그래프의 `supersedes`(대체) 관계로 해소한다 — 임베딩 유사도만으로는 "이 문서가 저 문서를 대체했다"는 사실을 표현할 수 없다.
- **복잡한 질의**는 단발 RAG로는 부족할 가능성이 크다 — 검색→중간 추론→재검색을 반복하는 에이전틱 루프가 필요한데, 이는 응답 지연·비용 증가라는 트레이드오프를 동반한다.
- **권한**: JIRA/Confluence의 비공개 스페이스를 봇이 그대로 노출하면 안 된다. 전사 크롤링 대신 화이트리스트 스페이스/프로젝트부터 시작한다.
- **피드백 어텐션 귀속 문제**: 답변 하나가 보통 여러 청크를 조합해서 나오기 때문에, 리액션 하나만으로 어떤 청크가 잘했는지 특정하기 어렵다 → 베이지안 스무딩 + 시간 감쇠 + 유저 다양성 보정이 필요하다.
- 사내 봇 특성상 피드백 절대량이 적어서, 소수의 목소리 큰 유저에게 가중치가 쏠릴 위험이 있다.

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
        └───────────────▶  기존 Mattermost 봇서버 ──▶ 사용자
                                  │
                    (리액션) ◀────┘
                                  │
                                  ▼
                     Feedback 어텐션·가중치 갱신 ──▶ Knowledge Store로 반영
```

신규로 만들어야 하는 것은 **Ingestion, Knowledge Store, Ranking Service, MCP Server, Feedback 모듈** 다섯 가지이고, Mattermost 쪽은 **기존 봇서버에 MCP 클라이언트를 붙이는 확장 작업**이다.

## 4. 컴포넌트별 구성 및 개발 항목

### A. Ingestion & Sync Pipeline — `effort: high`

API 3종의 인증·페이지네이션·레이트리밋이 제각각이고, 증분 동기화에서 누락·중복을 막는 로직이 실제로 가장 오래 걸리는 지점.

- [ ] JIRA REST API 커넥터 (이슈/댓글/상태변경 이력 수집)
- [ ] Confluence REST API 커넥터 (페이지 + 버전 이력, `version.when`/`version.number` 추적)
- [ ] Mattermost 채널 히스토리 백필 (기존 봇서버가 이미 갖고 있는 API 토큰/권한 재사용 검토)
- [ ] 변경 감지: 웹훅 우선, 미지원 구간은 폴링 + diff
- [ ] 청킹기 — 문서 구조(제목/코드블록) 보존한 분할
- [ ] 임베딩 배치 잡 (스케줄러)
- [ ] 메타데이터 태깅 — 출처 타입/작성자/생성·수정일/권한 스코프
- [ ] 권한 화이트리스트 설계 (수집 대상 스페이스/프로젝트 목록)

### B. Knowledge Store — `effort: medium`

- [ ] 벡터DB 프로비저닝 (pgvector 등)
- [ ] 스키마 설계: `documents`, `chunks`, `edges(references/supersedes)`, `feedback_scores`
- [ ] 업서트 API — 중복 방지 키(`source_type + source_id + chunk_index`)

### C. Hybrid Ranking Service — `effort: high`

네 가지 신호의 가중치 튜닝과, "오래됐지만 맞는 문서"를 억울하게 밀어내지 않는 균형이 핵심 난제.

```
score = sim(q, d) × time_decay(d) × authority(d) × feedback(d)
```

- [ ] 스코어링 함수 구현 (가중치는 설정값으로 분리해서 튜닝 가능하게)
- [ ] 그래프 확장 검색 (N-hop 관련 문서 탐색)
- [ ] `supersedes` 자동 감지 → 대체된 문서 하위 랭크/제외
- [ ] 복잡 질의용 멀티홉 에이전틱 검색 프로토타입 (검색 → 중간 요약 → 재검색)

### D. MCP Server — `effort: medium`

- [ ] MCP 서버 스캐폴딩
- [ ] 툴 스펙: `search_knowledge`, `get_related`, `get_document`, `submit_feedback`
- [ ] Ranking Service 연동
- [ ] Claude Desktop/Code로 로컬 연결 검증

### E. Mattermost 봇 통합 — `effort: low-medium` (봇서버는 기존 것 재사용)

- [ ] 기존 봇서버에 MCP 클라이언트 라이브러리 추가
- [ ] LLM 호출 시 MCP 툴(`search_knowledge` 등) attach
- [ ] 답변에 출처 링크(JIRA 티켓 / Confluence 페이지 / 원본 스레드) 포맷팅
- [ ] 답변 메시지에 리액션(👍/👎) 유도
- [ ] 리액션 이벤트 구독 → `submit_feedback` MCP 호출 연동
- [ ] 기존 메시지 핸들러와 신규 지식질의 핸들러 라우팅 분리 (기존 기능 회귀 방지)

### F. 피드백 어텐션 모듈 — `effort: medium-high`

- [ ] 답변 생성 시 실제 인용된 청크 ID 기록 (어텐션 매핑)
- [ ] 베이지안 스무딩 스코어 배치 계산: `(👍 + 1) / (전체 + 2)`
- [ ] 시간 감쇠 적용
- [ ] 동일 유저 반복 반응 디스카운트 (유저 다양성 보정)

### G. Observability/Admin — `effort: low`

- [ ] 자주 인용/무시되는 문서 대시보드
- [ ] 오래된 문서 업데이트 알림

## 5. 단계적 로드맵

1. **Phase 1** — JIRA/Confluence 우선 수집, 벡터검색 + 최신성 가중치만, MCP `search_knowledge` 툴 1개, 기존 봇에 연동 → 빠르게 가치 검증
2. **Phase 2** — 그래프 관계 + 출처 신뢰도 추가, `supersedes` 충돌 해소
3. **Phase 3** — 피드백 루프(어텐션 + 베이지안) + 멀티홉 에이전틱 검색

## 참고

전체 시스템 다이어그램은 별도로 [HLD 아티팩트](https://claude.ai/code/artifact/5a8c6a40-9a12-4158-8cbc-cdc67e19ad65)로 정리해뒀다.
