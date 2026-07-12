# Vector Embeddings & pgvector

Embedding은 텍스트(문장, 문단)를 의미를 보존한 채 고정 길이의 숫자 벡터로 변환한 것이다. 의미가 비슷한 문장일수록 벡터 공간에서 가까이 위치하게 되고, 이 "가까움"을 코사인 유사도(cosine similarity) 같은 수식으로 계산해서 **키워드가 정확히 일치하지 않아도 의미상 관련된 문서**를 찾을 수 있다.

## 왜 키워드 검색만으로는 부족한가

"연차 신청 방법"과 "휴가 어떻게 내요"는 키워드가 거의 안 겹치지만 의미는 같다. Embedding 기반 검색은 이런 케이스를 잡아낸다 — 이게 이 프로젝트의 Ranking Service에서 `sim(q, d)` 항이 하는 일이다.

## pgvector

PostgreSQL 확장(extension)으로, 벡터 컬럼 타입과 유사도 검색 연산자를 DB 안에 바로 추가해준다. 별도 벡터DB(Pinecone, Weaviate 등)를 운영하지 않고, 이미 쓰는 Postgres에 `documents`/`chunks` 테이블과 나란히 벡터 인덱스를 둘 수 있어 이 프로젝트처럼 규모가 크지 않은 사내 시스템에는 운영 부담이 적다.

## 이 프로젝트에서의 흐름

1. Ingestion이 문서를 청크(chunk) 단위로 쪼갠다
2. 각 청크를 embedding 모델에 통과시켜 벡터로 변환
3. pgvector 테이블에 벡터 + 메타데이터(출처/시각/권한) 함께 저장
4. 질의가 들어오면 질의도 같은 방식으로 embedding한 뒤, 코사인 유사도로 가까운 청크를 찾는다

---
[← 구현 계획으로 돌아가기](/notes/mattermost-second-brain-plan)
