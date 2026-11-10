# K-EDC 정렬 `data` 디렉터리

이 저장소의 `data`는 **제어 평면(Control Plane)** 과 **데이터 평면(Data Plane)** 역할을 폴더로 분리합니다. 런타임은 `data/control_plane/` 과 `data/data_plane/` 경로를 사용합니다 (`backend/api/frontend_routes.py`).

## 흐름 (요약)

```text
기업 A (데이터 보유)  →  제어 평면: 인증·카탈로그·계약·정책·승인
                              ↓ 승인
                         데이터 평면: 실제 샘플/전송 페이로드
                              →
기업 B (데이터 사용자)
```

## 디렉터리 구조

### `data/control_plane/` — 제어 평면 산출물


| 파일                                 | 역할                                                                  |
| ---------------------------------- | ------------------------------------------------------------------- |
| `catalog.json`                     | 데이터셋 카탈로그 (`dataset_id`, `schema_path`, `sample_path`, 산업 메타)       |
| `schemas.json`                     | **단일 스키마 번들**: 키는 `dataset_id` (`auto-ev-bms-v1` 등), 값은 JSON Schema |
| `assets.json`                      | 자산(EDC Asset) 시드 / 메타                                               |
| `contracts.json`                   | 계약 시드 (제공자·소비자·목적·상태)                                               |
| `vc_store.json`                    | VC(자격) 시드                                                           |
| `mappings/aas_mapping.json`        | 필드 → AAS Submodel / Property                                        |
| `mappings/edc_asset_profiles.json` | EDC 자산·ODRL 정책 템플릿 (향후 등록/협상 플로우 연동용)                               |
| `field_dictionary.json`            | 필드 사전(별칭·단위·범위·소스·주기·계층) + 전처리/정규화 규칙                               |


**참고:** `data/schemas/*.schema.json` 은 **deprecated** — 내용은 `control_plane/schemas.json`에 통합되었습니다. 호환을 위해 파일은 유지할 수 있습니다.

### `data/data_plane/samples/` — 데이터 평면 샘플


| 파일                             | 역할                                        |
| ------------------------------ | ----------------------------------------- |
| `auto_ev_bms.sample.json`      | EV BMS 텔레메트리 샘플 레코드                       |
| `battery_pass.sample.json`     | Battery Pass 샘플                           |
| `steel_pcf.sample.json`        | 철강 PCF 샘플                                 |
| `sample_telemetry.legacy.json` | 구 `sample_telemetry.json` 대체 레거시 fallback |


## API에서의 소비 (현재)


| HTTP                                     | 소스 파일                                                                            |
| ---------------------------------------- | -------------------------------------------------------------------------------- |
| `GET /ui/api/catalog`                    | `control_plane/catalog.json`                                                     |
| `GET /ui/api/assets`                     | `control_plane/assets.json`                                                      |
| `GET /ui/api/contracts`                  | `control_plane/contracts.json`                                                   |
| `GET /ui/api/credentials`                | `control_plane/vc_store.json`                                                    |
| `GET /ui/api/analyze-sample`             | `data_plane/samples/*.json` → fallback `data/sample_telemetry/*.json` → 레거시 JSON |
| `GET /ui/api/dashboard-summary?sample=…` | 위와 동일 샘플 경로                                                                      |


`schemas.json`은 현재 **검증 런타임에 직접 연결되지 않음** — 카탈로그의 `schema_path`가 `data/control_plane/schemas.json#<dataset_id>` 형태로 참조하도록 정리되어 있으며, 이후 `/metadata/extract` 등에서 로드·검증 연동 예정.
`field_dictionary.json`은 `backend/metadata/extractor.py`에서 읽어 동의어 정규화 및 기본 시맨틱 태깅에 활용됩니다.

## `catalog.json`의 `schema_path` 규칙

- 형식: `data/control_plane/schemas.json#<dataset_id>`
- 예: `data/control_plane/schemas.json#auto-ev-bms-v1`

로더가 URI fragment를 파싱하지는 않지만, 사람/도구가 번들에서 해당 키를 찾는 기준으로 사용합니다.