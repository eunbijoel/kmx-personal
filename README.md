[한국어](README.md) | [English](README_EN.md)

# KMX Platform — 한국형 Manufacturing-X 데이터 스페이스 플랫폼

> **데이터 스페이스 + AI + 거버넌스 통합 플랫폼 레퍼런스 구현**  
> Korean Manufacturing-X (KMX) Data Space Platform

---

## 아키텍처 개요

```mermaid
flowchart TB
  subgraph Actors["Actors"]
    U["사용자/기업"]
    A["AI 에이전트"]
    C["외부 커넥터 (연합)"]
  end

  subgraph Gov["Identity & Governance Layer"]
    DID["DID/VC 관리"]
    ODRL["ODRL 정책엔진"]
    CM["Contract Manager"]
  end

  subgraph DS["Data Space Layer"]
    CP["Control Plane<br/>계약 협상 · 정책 검사 · 커넥터 라우팅"]
    DP["Data Plane<br/>P2P 전송 · 포맷 변환 · 데이터 주권"]
    CP <--> DP
  end

  subgraph AI["AI Layer"]
    AIM["예지보전 · 품질검사 · 공정최적화<br/>수요예측 · 에너지최적"]
  end

  subgraph DI["Data Intelligence Layer"]
    META["메타데이터 자동 추출"]
    VEC["벡터 검색 · 자연어 검색"]
    ONT["온톨로지 매핑"]
    CAT["DCAT 카탈로그 · 데이터셋 등록"]
  end

  subgraph CH["Clearing House"]
    LOG["해시체인 감사로그 · 사용량 정산"]
  end

  U --> Gov
  A --> Gov
  C --> Gov
  Gov --> DS
  DS --> AI
  DS --> DI
  DS --> CH
```

---

## 데이터 흐름 다이어그램

```mermaid
flowchart TD
  R["사용자 요청"] --> S1

  S1["Step 1: Agent 실행 (선택)<br/>Delegation VC 발급"] --> S2
  S2["Step 2: DID/VC 인증<br/>DID 확인 · VC 검증 · Ed25519 서명"] -->|인증 통과| S3
  S3["Step 3: Contract 확인<br/>계약 존재 · ACTIVE · 만료 여부"] -->|계약 유효| S4
  S4["Step 4: ODRL 정책 검사<br/>행위 허용 · 제약 평가 · Prohibition"] -->|정책 허용| S5
  S5["Step 5: Data Plane 전송<br/>P2P · JSON/CSV 변환 · 페이로드 해시"] --> S6
  S6["Step 6: Clearing House 기록<br/>해시체인 · 위변조 방지 · 정산"] --> OUT
  OUT["응답 반환<br/>데이터 + 전송 ID + 해시"]
```

---

## 프로젝트 구조

```
kmx/
├── backend/
│   ├── main.py                    # FastAPI 메인 앱
│   ├── requirements.txt
│   ├── api/
│   │   ├── routes.py              # LIVE REST API (인라인)
│   │   ├── frontend_routes.py     # Jinja UI + /ui/api/*
│   │   └── manufacturing_routes.py# 제조 데이터 → AAS 파이프라인 API
│   ├── manufacturing/             # 전처리 · 품질 · AAS · 카탈로그
│   ├── metadata/
│   │   └── extractor.py           # 필드 사전 기반 메타데이터 추출
│   └── db/
│       ├── models.py              # SQLAlchemy ORM 모델
│       └── database.py            # DB 연결/초기화
├── frontend/                      # Jinja 템플릿 + static
├── data/
│   ├── control_plane/             # 카탈로그 · 매핑 · field dictionary
│   ├── data_plane/samples/        # 샘플 데이터
│   └── aas_store/                 # AAS draft / catalog registry (런타임)
├── tests/                         # pytest
├── docker/
│   ├── Dockerfile.backend
│   └── docker-compose.yml
└── README.md
```

---

## 빠른 시작

### 방법 1: 직접 실행 (SQLite - 개발용)

```bash
# 1. 저장소 클론
git clone https://github.com/eunbijoel/kmx-personal.git
cd kmx-personal/backend

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 서버 실행
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 4. API 문서 확인
# http://localhost:8000/docs
# Manufacturing UI: http://localhost:8000/manufacturing
```

### Windows PowerShell 안정 실행 런북 (문제 재발 시)

```powershell
# 0) backend로 이동
cd C:\Users\keti\OneDrive\문서\GitHub\kmx\backend

# 1) 가상환경 생성 (최초 1회)
py -3 -m venv .venv

# 2) 가상환경 python으로 의존성 설치 (activate 없이도 가능)
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3) 서버 실행 (권장: 127.0.0.1 바인딩)
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

체크포인트:

- `.\.venv\Scripts\python.exe` 경로 오류가 나면 현재 위치가 `backend`인지 먼저 확인합니다.
- `http://127.0.0.1:8000/docs`가 아닌 다른 화면이 뜨면 8000 포트를 다른 앱이 점유한 상태일 수 있습니다.
- 점유 확인: `netstat -ano | findstr :8000`

### 방법 2: Docker Compose

```bash
# 레포 루트에서 Compose 파일 경로 지정
docker compose -f docker/docker-compose.yml up -d

# 로그 확인
docker compose -f docker/docker-compose.yml logs -f kmx-backend

# 중지
docker compose -f docker/docker-compose.yml down
```

### 방법 3: 테스트

```bash
# 레포 루트에서
python -m pytest tests -v
```

---

## API 명세

### 베이스 URL: `http://localhost:8000/api/v1`

#### 🔐 Identity (DID/VC)


| Method | Path                          | 설명            |
| ------ | ----------------------------- | ------------- |
| POST   | `/identity/did`               | DID 생성        |
| GET    | `/identity/did/{did}`         | DID 조회        |
| POST   | `/identity/vc`                | VC 발급         |
| GET    | `/identity/vc/{vc_id}/verify` | VC 검증         |
| POST   | `/identity/vc/delegate`       | 에이전트 위임 VC 발급 |


#### 🔗 Connector (EDC)


| Method | Path                       | 설명         |
| ------ | -------------------------- | ---------- |
| POST   | `/connector/register`      | 커넥터 등록     |
| GET    | `/connector/list`          | 커넥터 목록     |
| POST   | `/connector/negotiate`     | 계약 협상 시작   |
| POST   | `/connector/data/register` | 데이터셋 등록    |
| POST   | `/connector/data/transfer` | 데이터 P2P 전송 |


#### 📋 Policy (ODRL)


| Method | Path               | 설명    |
| ------ | ------------------ | ----- |
| POST   | `/policy/`         | 정책 생성 |
| GET    | `/policy/`         | 정책 목록 |
| POST   | `/policy/evaluate` | 정책 평가 |


#### 📝 Contract


| Method | Path                    | 설명    |
| ------ | ----------------------- | ----- |
| POST   | `/contract/`            | 계약 생성 |
| POST   | `/contract/{id}/sign`   | 계약 서명 |
| GET    | `/contract/{id}/verify` | 계약 검증 |


#### 🏷️ Metadata


| Method | Path                          | 설명          |
| ------ | ----------------------------- | ----------- |
| POST   | `/metadata/extract`           | 메타데이터 추출/저장 |
| GET    | `/metadata/`                  | 데이터셋 카탈로그   |
| POST   | `/metadata/ontology/map`      | 온톨로지 매핑     |
| GET    | `/metadata/ontology/concepts` | 온톨로지 개념 목록  |


#### 🤖 AI


| Method | Path                         | 설명       |
| ------ | ---------------------------- | -------- |
| POST   | `/ai/predict`                | AI 예측 실행 |
| GET    | `/ai/models`                 | 모델 목록    |
| GET    | `/ai/models/{type}/metadata` | 모델 메타데이터 |
| GET    | `/ai/models/{type}/health`   | 모델 상태    |


#### 🤖 Agent (Agentic AI)


| Method | Path                  | 설명         |
| ------ | --------------------- | ---------- |
| POST   | `/agent/initialize`   | 에이전트 초기화   |
| POST   | `/agent/delegate`     | 권한 위임      |
| POST   | `/agent/auto-catalog` | 자동 카탈로그 생성 |
| GET    | `/agent/health`       | 에이전트 상태    |


#### 🔍 Search


| Method | Path                         | 설명          |
| ------ | ---------------------------- | ----------- |
| POST   | `/search/datasets`           | 자연어 데이터셋 검색 |
| GET    | `/search/datasets?q={query}` | 키워드 검색      |
| POST   | `/search/ontology`           | 온톨로지 기반 검색  |


#### 📊 Clearing House


| Method | Path                          | 설명          |
| ------ | ----------------------------- | ----------- |
| GET    | `/clearinghouse/logs`         | 전송 로그 조회    |
| GET    | `/clearinghouse/verify-chain` | 해시체인 무결성 검증 |
| GET    | `/clearinghouse/usage-report` | 사용량 정산 보고서  |


---

## 시나리오 예시: 현대자동차 → 삼성전자 데이터 교환

```bash
BASE="http://localhost:8000/api/v1"

# 1. DID 생성
PROVIDER=$(curl -s -X POST $BASE/identity/did \
  -H "Content-Type: application/json" \
  -d '{"controller":"현대자동차-공급망","entity_type":"connector"}')
PROVIDER_DID=$(echo $PROVIDER | python3 -c "import sys,json; print(json.load(sys.stdin)['did'])")

CONSUMER=$(curl -s -X POST $BASE/identity/did \
  -H "Content-Type: application/json" \
  -d '{"controller":"삼성전자-구미공장","entity_type":"human"}')
CONSUMER_DID=$(echo $CONSUMER | python3 -c "import sys,json; print(json.load(sys.stdin)['did'])")

# 2. 정책 생성
POLICY=$(curl -s -X POST $BASE/policy/ \
  -H "Content-Type: application/json" \
  -d "{
    \"title\": \"제조 데이터 공유 정책\",
    \"target\": \"supply-chain-dataset-001\",
    \"assigner\": \"$PROVIDER_DID\",
    \"permissions\": [{
      \"action\": \"use\",
      \"constraints\": [
        {\"leftOperand\": \"purpose\", \"operator\": \"eq\", \"rightOperand\": \"manufacturing\"}
      ]
    }],
    \"prohibitions\": [{\"action\": \"distribute\", \"constraints\": []}]
  }")
POLICY_ID=$(echo $POLICY | python3 -c "import sys,json; print(json.load(sys.stdin)['uid'])")

# 3. 커넥터 등록 및 계약 협상 → 데이터 전송
# (이후 negotiate, sign, transfer 순으로 진행)

echo "Provider DID: $PROVIDER_DID"
echo "Policy ID: $POLICY_ID"
```

---

## 표준 준수 사항


| 표준                        | 구현 내용                                           |
| ------------------------- | ----------------------------------------------- |
| **W3C DID Core 1.0**      | `did:kmx:` 메서드, DID Document 구조                 |
| **W3C VC Data Model 1.1** | VC 발급/검증, Ed25519 서명                            |
| **ODRL 2.2**              | Permission/Prohibition/Obligation, ODRL JSON-LD |
| **DCAT 2**                | 데이터셋 메타데이터, Distribution 구조                     |
| **IDS-RAM**               | Control/Data Plane 분리, Contract 협상 프로토콜         |
| **ISO 62443**             | 산업 사이버보안 (아키텍처 반영)                              |


---

## 환경 변수


| 변수             | 기본값                                     | 설명             |
| -------------- | --------------------------------------- | -------------- |
| `DATABASE_URL` | `sqlite+aiosqlite:///./kmx_platform.db` | DB 연결 문자열      |
| `CONNECTOR_ID` | 자동생성                                    | 이 인스턴스의 커넥터 ID |


---

## 라이선스

Apache 2.0 — KMX Platform은 오픈소스 레퍼런스 구현입니다.