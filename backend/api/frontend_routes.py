from pathlib import Path
import csv
import io
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import DataContract, DatasetMetadata

router = APIRouter()

ROOT_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(ROOT_DIR / "frontend" / "templates"))
DATA_ROOT = ROOT_DIR / "data"
CONTROL_PLANE_DIR = DATA_ROOT / "control_plane"
DATA_PLANE_DIR = DATA_ROOT / "data_plane"
DATA_PLANE_SAMPLES_DIR = DATA_PLANE_DIR / "samples"
# Legacy sample folder (pre K-EDC layout)
SAMPLE_TELEMETRY_DIR = DATA_ROOT / "sample_telemetry"
LEGACY_SAMPLE_TELEMETRY_PATHS = (
    DATA_PLANE_SAMPLES_DIR / "sample_telemetry.legacy.json",
    DATA_ROOT / "sample_telemetry.json",
)
SAMPLE_KEY_TO_FILENAME = {
    "auto_ev_bms": "auto_ev_bms.sample.json",
    "battery_pass": "battery_pass.sample.json",
    "steel_pcf": "steel_pcf.sample.json",
}
PRIMARY_TELEMETRY_SAMPLE_PATH = DATA_PLANE_SAMPLES_DIR / "auto_ev_bms.sample.json"
SAMPLE_DATASET_ID_MAP = {
    "auto_ev_bms": "auto-ev-bms-v1",
    "battery_pass": "battery-pass-v1",
    "steel_pcf": "steel-pcf-v1",
}


def _pick_existing_path(primary: Path, fallback: Path) -> Path:
    if primary.exists():
        return primary
    if fallback.exists():
        return fallback
    return primary


ASSETS_STORE_PATH = _pick_existing_path(CONTROL_PLANE_DIR / "assets.json", DATA_ROOT / "assets.json")
CONTRACTS_STORE_PATH = _pick_existing_path(CONTROL_PLANE_DIR / "contracts.json", DATA_ROOT / "contracts.json")
VC_STORE_PATH = _pick_existing_path(CONTROL_PLANE_DIR / "vc_store.json", DATA_ROOT / "vc_store.json")
CATALOG_PATH = _pick_existing_path(CONTROL_PLANE_DIR / "catalog.json", DATA_ROOT / "catalog.json")
AAS_MAPPING_PATH = _pick_existing_path(
    CONTROL_PLANE_DIR / "mappings" / "aas_mapping.json",
    DATA_ROOT / "mappings" / "aas_mapping.json",
)
# EDC asset profiles: data/control_plane/mappings/edc_asset_profiles.json (see data/README.md)
SCHEMAS_BUNDLE_PATH = CONTROL_PLANE_DIR / "schemas.json"
FIELD_MEANINGS = {
    "battery_id": "배터리 고유 식별자(규제/추적 기준 키)",
    "manufacturer_id": "제조사 식별자(BPN 등 공급망 식별 체계)",
    "manufacturing_date": "배터리 제조 완료 일자(ISO 날짜)",
    "manufacturing_place": "제조 사이트/공장 식별 정보",
    "capacity_kwh": "정격 용량(kWh)",
    "nominal_voltage_v": "공칭 전압(V)",
    "chemistry": "배터리 화학계열(NCM/LFP 등)",
    "soh_at_delivery_pct": "출하 시점 SOH(건전성) 비율",
    "cycle_life_expected": "설계 기대 수명 사이클 수",
    "carbon_footprint_kg_co2eq": "제품 기준 탄소발자국(kgCO2eq)",
    "recycled_content_cobalt_pct": "코발트 재활용 원료 비중(%)",
    "recycled_content_lithium_pct": "리튬 재활용 원료 비중(%)",
    "recycled_content_nickel_pct": "니켈 재활용 원료 비중(%)",
    "responsible_mining_cert": "책임 광물 조달 인증 식별자",
    "hazardous_substance_list": "유해물질 목록(SVHC 등)",
    "catena_cx_digital_twin_id": "Catena-X 디지털 트윈 식별자",
    "record_id": "레코드 고유 식별자",
    "produced_at": "데이터 생성 시각(UTC)",
    "vin": "차량 식별번호(VIN)",
    "pack_id": "배터리 팩 식별자",
    "soc_pct": "SOC(충전 상태) 비율",
    "soh_pct": "SOH(건전성) 비율",
    "voltage_pack_v": "팩 전압(V)",
    "current_a": "팩 전류(A)",
    "temperature_c": "온도(C)",
    "vibration_mm_s": "진동 속도(mm/s)",
    "power_watts": "전력(W)",
    "status": "운전 상태(RUNNING/WARNING/FAULT)",
    "alarms": "활성 알람 코드 목록",
    "reporting_year": "보고 기준 연도",
    "facility_id": "사업장/공장 식별자",
    "scope1_emissions_mt": "Scope 1 직접배출량(MtCO2eq)",
    "scope2_emissions_mt": "Scope 2 간접배출량(MtCO2eq)",
    "co2_intensity_tcs": "조강 톤당 탄소집약도(tCO2/tcs)",
    "crude_steel_prod_mt": "조강 생산량(Mt)",
    "energy_consumption_gj": "총 에너지 사용량(GJ)",
    "blast_furnace_route_pct": "고로(BF) 생산 비중(%)",
    "eaf_route_pct": "전기로(EAF) 생산 비중(%)",
    "renewable_energy_pct": "재생에너지 사용 비중(%)",
    "cbam_carbon_content": "CBAM 신고용 탄소함량",
}

IN_MEMORY_ASSETS: list[dict[str, Any]] = []
IN_MEMORY_EVENTS: list[dict[str, Any]] = []
IN_MEMORY_ACCESS_REQUESTS: list[dict[str, Any]] = []
FALLBACK_ROBOT_RECORDS: list[dict[str, Any]] = [
    {
        "robot_id": "cobot-05",
        "station_id": "station-02",
        "status": "WARNING",
        "temperature_c": 70.5,
        "vibration_mm_s": 4.2,
        "power_watts": 470.5,
        "alarms": ["ALM-TEMP-HIGH"],
    },
    {
        "robot_id": "cobot-08",
        "station_id": "station-05",
        "status": "FAULT",
        "temperature_c": 88.0,
        "vibration_mm_s": 12.0,
        "power_watts": 100.0,
        "alarms": ["ALM-OVERHEAT", "ALM-VIB-HIGH"],
    },
    {
        "robot_id": "cobot-01",
        "station_id": "station-07",
        "status": "RUNNING",
        "temperature_c": 42.8,
        "vibration_mm_s": 1.9,
        "power_watts": 438.7,
        "alarms": [],
    },
]


class RegisterPayload(BaseModel):
    name: str
    description: str
    fields: list[str]
    owner_group: str
    usage_scope: str
    usage_days: int
    ai_models: list[str]


class AccessRequestPayload(BaseModel):
    asset_id: str
    requester: str
    purpose: str
    usage_scope: str
    usage_days: int


def _render(request: Request, template: str, title: str, **context: object) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={"request": request, "title": title, **context},
    )


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return _render(request, "dashboard.html", "대시보드")


@router.get("/datasets", response_class=HTMLResponse)
async def datasets(request: Request) -> HTMLResponse:
    return _render(request, "datasets.html", "데이터 등록/검색")


@router.get("/identity", response_class=HTMLResponse)
async def identity(request: Request) -> HTMLResponse:
    return _render(request, "identity.html", "계정/신원")


@router.get("/contracts", response_class=HTMLResponse)
async def contracts(request: Request) -> HTMLResponse:
    return _render(request, "contracts.html", "사용 설정")


@router.get("/agents", response_class=HTMLResponse)
async def agents(request: Request) -> HTMLResponse:
    return _render(request, "agents.html", "자동화 도우미")


@router.get("/logs", response_class=HTMLResponse)
async def logs(request: Request) -> HTMLResponse:
    return _render(request, "logs.html", "사용 내역/정산")


@router.get("/manufacturing", response_class=HTMLResponse)
async def manufacturing(request: Request) -> HTMLResponse:
    return _render(request, "manufacturing.html", "Manufacturing AAS Pipeline")


@router.get("/ui/api/search")
async def ui_search(q: str) -> JSONResponse:
    query = q.strip() or "온도"
    items = [
        "ALD 챔버 온도 데이터",
        "식각 공정 온도 데이터",
        "품질 영향 온도 데이터",
    ]
    matched = [item for item in items if any(token in item for token in query.split())]
    return JSONResponse({"query": query, "results": matched or items, "recommended": items[:2]})


@router.post("/ui/api/analyze")
async def ui_analyze() -> JSONResponse:
    return JSONResponse(
        {
            "description": "반도체 장비 센서 로그 데이터입니다.",
            "column_meanings": ["온도: 챔버 내부 온도", "압력: 공정 압력", "진동: 설비 상태 신호"],
            "data_meaning": ["온도 -> 설비 열상태", "압력 -> 공정 안정성", "진동 -> 이상 징후"],
            "available_models": ["예지보전", "품질분석", "공정최적화"],
        }
    )


def _infer_models(fields: list[str]) -> list[dict[str, str]]:
    field_set = {field.lower() for field in fields}
    models = []
    if {"temperature", "vibration", "pressure"} & field_set:
        models.append({"name": "고장 예측", "status": "바로 사용 가능"})
    if {"defect_rate", "quality", "thickness"} & field_set:
        models.append({"name": "품질 분석", "status": "바로 사용 가능"})
    if {"energy_kwh", "power"} & field_set:
        models.append({"name": "에너지 최적화", "status": "추가 데이터 필요"})
    if not models:
        models = [{"name": "기본 이상 감지", "status": "바로 사용 가능"}]
    return models


def _load_sample_records(sample: str = "auto_ev_bms") -> tuple[list[dict[str, Any]], str]:
    fname = SAMPLE_KEY_TO_FILENAME.get(sample, SAMPLE_KEY_TO_FILENAME["auto_ev_bms"])
    sample_candidates = [
        _pick_existing_path(DATA_PLANE_SAMPLES_DIR / fname, SAMPLE_TELEMETRY_DIR / fname),
    ]
    seen: set[Path] = set()
    ordered_paths: list[Path] = []
    for p in sample_candidates:
        if p not in seen:
            ordered_paths.append(p)
            seen.add(p)
    for legacy_path in LEGACY_SAMPLE_TELEMETRY_PATHS:
        if legacy_path not in seen:
            ordered_paths.append(legacy_path)
            seen.add(legacy_path)

    for sample_path in ordered_paths:
        if not sample_path.exists():
            continue
        try:
            payload = json.loads(sample_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows = [row for row in payload if isinstance(row, dict)]
                if rows:
                    return rows, sample_path.name
            if isinstance(payload, dict):
                return [payload], sample_path.name
        except Exception:
            continue
    return [], fname


def _to_dashboard_row(row: dict[str, Any], sample: str) -> dict[str, Any]:
    if sample == "battery_pass":
        soh = float(row.get("soh_at_delivery_pct", 0.0))
        carbon = float(row.get("carbon_footprint_kg_co2eq", 0.0))
        capacity = float(row.get("capacity_kwh", 0.0))
        return {
            "robot_id": row.get("battery_id", "battery"),
            "station_id": row.get("manufacturing_place", "-"),
            "status": "WARNING" if soh < 99.0 else "RUNNING",
            "temperature_c": max(0.0, 100.0 - soh),
            "vibration_mm_s": max(0.1, carbon / 40.0),
            "power_watts": capacity * 10.0,
            "alarms": [] if soh >= 99.0 else ["ALM-SOH-LOW"],
            "produced_at": f"{row.get('manufacturing_date', '')}T00:00:00Z",
        }
    if sample == "steel_pcf":
        intensity = float(row.get("co2_intensity_tcs", 0.0))
        renewable = float(row.get("renewable_energy_pct", 0.0))
        energy = float(row.get("energy_consumption_gj", 0.0))
        return {
            "robot_id": row.get("facility_id", "steel-site"),
            "station_id": str(row.get("reporting_year", "-")),
            "status": "WARNING" if intensity > 2.0 else "RUNNING",
            "temperature_c": intensity * 40.0,
            "vibration_mm_s": max(0.1, (100.0 - renewable) / 25.0),
            "power_watts": energy / 400000.0,
            "alarms": [] if intensity <= 2.0 else ["ALM-PCF-HIGH"],
            "produced_at": f"{row.get('reporting_year', '')}-01-01T00:00:00Z",
        }
    return row


def _load_store_items(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        rows = payload.get(key, [])
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _load_catalog_items() -> list[dict[str, Any]]:
    if not CATALOG_PATH.exists():
        return []
    try:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    datasets = payload.get("datasets", []) if isinstance(payload, dict) else []
    if not isinstance(datasets, list):
        return []
    return [row for row in datasets if isinstance(row, dict)]


def _get_catalog_dataset(dataset_id: str | None) -> dict[str, Any] | None:
    if not dataset_id:
        return None
    for row in _load_catalog_items():
        if row.get("dataset_id") == dataset_id:
            return row
    return None


def _load_aas_field_map(dataset_id: str | None) -> dict[str, dict[str, str]]:
    if not dataset_id or not AAS_MAPPING_PATH.exists():
        return {}
    try:
        payload = json.loads(AAS_MAPPING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    mappings = payload.get("mappings", []) if isinstance(payload, dict) else []
    if not isinstance(mappings, list):
        return {}
    for block in mappings:
        if not isinstance(block, dict) or block.get("dataset_id") != dataset_id:
            continue
        result: dict[str, dict[str, str]] = {}
        field_map = block.get("field_map", [])
        if not isinstance(field_map, list):
            return {}
        for row in field_map:
            if not isinstance(row, dict):
                continue
            field = row.get("field")
            if isinstance(field, str):
                result[field] = {
                    "aas_submodel": str(row.get("aas_submodel", "")),
                    "aas_property": str(row.get("aas_property", "")),
                }
        return result
    return {}


def _meaning_for_field(field: str) -> str:
    key = field.strip()
    if key in FIELD_MEANINGS:
        return FIELD_MEANINGS[key]
    lowered = key.lower()
    if "temp" in lowered:
        return "온도 관련 측정값"
    if "vib" in lowered:
        return "진동/상태 건전성 지표"
    if "power" in lowered or "energy" in lowered:
        return "에너지/전력 지표"
    if "date" in lowered or "time" in lowered:
        return "시간/일자 정보"
    return f"{field} 필드"


def _to_time_label(iso_text: str | None) -> str:
    if not iso_text:
        return "-"
    try:
        stamp = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
        return stamp.strftime("%H:%M:%S")
    except Exception:
        return iso_text


def _analyze_payload(payload: Any, file_name: str) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            raise HTTPException(status_code=400, detail="JSON 배열이 비어 있습니다.")
        sample = payload[0]
    elif isinstance(payload, dict):
        sample = payload
    else:
        raise HTTPException(status_code=400, detail="지원하지 않는 JSON 형식입니다.")

    fields = list(sample.keys())
    mapped = []
    for field in fields:
        lowered = field.lower()
        if "temp" in lowered:
            mapped.append("온도")
        elif "press" in lowered:
            mapped.append("압력")
        elif "vib" in lowered:
            mapped.append("진동")
        elif "power" in lowered:
            mapped.append("전력")
        elif "cycle" in lowered:
            mapped.append("사이클 시간")
        elif "time" in lowered or "date" in lowered:
            mapped.append("타임스탬프")
        else:
            mapped.append(field)

    return {
        "name": Path(file_name).stem,
        "description": f"{Path(file_name).stem} 로봇 텔레메트리 데이터입니다.",
        "fields": fields,
        "mapped_meanings": mapped,
        "ai_models": _infer_models(fields),
        "sample": sample,
    }


@router.post("/ui/api/analyze-upload")
async def analyze_upload(file: UploadFile) -> JSONResponse:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    file_name = file.filename or f"telemetry-{uuid.uuid4().hex[:6]}"
    text = raw.decode("utf-8", errors="ignore")

    if file_name.lower().endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        first = next(reader, None)
        if first is None:
            raise HTTPException(status_code=400, detail="CSV 데이터가 비어 있습니다.")
        result = _analyze_payload(first, file_name)
    else:
        payload = json.loads(text)
        result = _analyze_payload(payload, file_name)
    return JSONResponse(result)


@router.get("/ui/api/analyze-sample")
async def analyze_sample(sample: str = "auto_ev_bms") -> JSONResponse:
    records, source_file = _load_sample_records(sample)
    dataset_id = SAMPLE_DATASET_ID_MAP.get(sample, "auto-ev-bms-v1")
    if not records:
        raise HTTPException(
            status_code=404,
            detail=f"샘플 데이터를 찾을 수 없습니다: sample={sample}, expected={source_file}",
        )
    result = _analyze_payload(records, f"sample_telemetry/{source_file}")
    dataset_meta = _get_catalog_dataset(dataset_id) or {}
    if dataset_meta.get("title"):
        result["name"] = dataset_meta["title"]
        result["description"] = f"{dataset_meta['title']} 샘플 데이터"
    fields = result.get("fields", [])
    result["mapped_meanings"] = [_meaning_for_field(field) for field in fields]
    result["field_meanings"] = [{"field": field, "meaning": _meaning_for_field(field)} for field in fields]
    aas_map = _load_aas_field_map(dataset_id)
    result["sample"] = {"key": sample, "dataset_id": dataset_id, "source_file": source_file}
    result["aas_mappings"] = [
        {
            "field": field,
            "aas_submodel": aas_map[field]["aas_submodel"],
            "aas_property": aas_map[field]["aas_property"],
        }
        for field in result.get("fields", [])
        if field in aas_map
    ]
    result["mapping_summary"] = {
        "mapped_count": len(result["aas_mappings"]),
        "total_fields": len(fields),
    }
    return JSONResponse(result)


@router.get("/ui/api/catalog")
async def ui_catalog() -> JSONResponse:
    datasets = _load_catalog_items()
    return JSONResponse({"count": len(datasets), "items": datasets})


@router.get("/ui/api/assets")
async def ui_assets() -> JSONResponse:
    rows = _load_store_items(ASSETS_STORE_PATH, "assets")
    return JSONResponse({"count": len(rows), "items": rows})


@router.get("/ui/api/contracts")
async def ui_contracts() -> JSONResponse:
    rows = _load_store_items(CONTRACTS_STORE_PATH, "contracts")
    return JSONResponse({"count": len(rows), "items": rows})


@router.get("/ui/api/credentials")
async def ui_credentials() -> JSONResponse:
    rows = _load_store_items(VC_STORE_PATH, "credentials")
    return JSONResponse({"count": len(rows), "items": rows})


@router.post("/ui/api/register")
async def register_dataset(payload: RegisterPayload) -> JSONResponse:
    asset_id = f"asset-{uuid.uuid4().hex[:10]}"

    saved_to = "memory"
    if AsyncSessionLocal is not None:
        async with AsyncSessionLocal() as session:
            row = DatasetMetadata(
                dataset_id=asset_id,
                title=payload.name,
                description=payload.description,
                owner_did=f"group:{payload.owner_group}",
                data_type="JSON",
                columns=payload.fields,
                keywords=payload.fields,
                dcat_metadata={
                    "usage_scope": payload.usage_scope,
                    "usage_days": payload.usage_days,
                    "ai_models": payload.ai_models,
                },
                access_url=f"/datasets/{asset_id}",
            )
            session.add(row)
            await session.commit()
            saved_to = "database"

    IN_MEMORY_ASSETS.append(
        {
            "asset_id": asset_id,
            "name": payload.name,
            "description": payload.description,
            "fields": payload.fields,
            "usage_scope": payload.usage_scope,
            "usage_days": payload.usage_days,
            "created_at": datetime.utcnow().isoformat(),
        }
    )
    IN_MEMORY_EVENTS.insert(
        0,
        {
            "message": f"{payload.name} 등록 완료",
            "time": "방금 전",
            "type": "등록",
        },
    )
    IN_MEMORY_EVENTS[:] = IN_MEMORY_EVENTS[:8]

    return JSONResponse({"asset_id": asset_id, "saved_to": saved_to})


@router.post("/ui/api/access-request")
async def create_access_request(payload: AccessRequestPayload) -> JSONResponse:
    request_id = f"req-{uuid.uuid4().hex[:10]}"
    row = {
        "request_id": request_id,
        "asset_id": payload.asset_id,
        "requester": payload.requester,
        "purpose": payload.purpose,
        "usage_scope": payload.usage_scope,
        "usage_days": payload.usage_days,
        "status": "PENDING",
        "created_at": datetime.utcnow().isoformat(),
    }
    IN_MEMORY_ACCESS_REQUESTS.insert(0, row)
    IN_MEMORY_EVENTS.insert(
        0,
        {
            "message": f"{payload.requester} 사용 요청 접수 ({payload.asset_id})",
            "time": "방금 전",
            "type": "요청",
        },
    )
    IN_MEMORY_EVENTS[:] = IN_MEMORY_EVENTS[:8]
    return JSONResponse({"request_id": request_id, "status": "PENDING"})


@router.post("/ui/api/access-request/{request_id}/decision")
async def decide_access_request(request_id: str, action: str) -> JSONResponse:
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="action must be approve or reject")
    for row in IN_MEMORY_ACCESS_REQUESTS:
        if row["request_id"] == request_id:
            if row["status"] != "PENDING":
                return JSONResponse({"request_id": request_id, "status": row["status"]})
            row["status"] = "APPROVED" if action == "approve" else "REJECTED"
            IN_MEMORY_EVENTS.insert(
                0,
                {
                    "message": f"{request_id} {('승인' if action == 'approve' else '거절')} 처리",
                    "time": "방금 전",
                    "type": "승인" if action == "approve" else "거절",
                },
            )
            IN_MEMORY_EVENTS[:] = IN_MEMORY_EVENTS[:8]
            return JSONResponse({"request_id": request_id, "status": row["status"]})
    raise HTTPException(status_code=404, detail="request not found")


@router.get("/ui/api/dashboard-summary")
async def dashboard_summary(sample: str = "auto_ev_bms") -> JSONResponse:
    assets = list(IN_MEMORY_ASSETS)

    if AsyncSessionLocal is not None:
        async with AsyncSessionLocal() as session:
            rows = await session.scalars(select(DatasetMetadata).order_by(DatasetMetadata.created_at.desc()).limit(20))
            for row in rows:
                assets.append(
                    {
                        "asset_id": row.dataset_id,
                        "name": row.title,
                        "description": row.description,
                        "fields": row.columns or [],
                    }
                )

    sample_records, source_file = _load_sample_records(sample)
    sample_records = sample_records or FALLBACK_ROBOT_RECORDS
    dashboard_rows = [_to_dashboard_row(row, sample) for row in sample_records]
    if not assets and sample_records:
        sample_fields = list(sample_records[0].keys())
        assets.append(
            {
                "asset_id": "sample-telemetry",
                "name": "로봇 텔레메트리 샘플",
                "description": "sample_telemetry/auto_ev_bms.sample.json 기반",
                "fields": sample_fields,
            }
        )

    telemetry_assets = [
        a
        for a in assets
        if any(
            k in " ".join(a.get("fields", [])).lower()
            for k in ["temp", "press", "vib", "power", "cycle"]
        )
    ]
    total_assets = len(assets)
    telemetry_count = len(telemetry_assets)
    pending_items: list[dict[str, str]] = []
    if AsyncSessionLocal is not None:
        async with AsyncSessionLocal() as session:
            pending_rows = await session.scalars(
                select(DataContract)
                .where(DataContract.status == "PENDING")
                .order_by(DataContract.created_at.desc())
                .limit(3)
            )
            for row in pending_rows:
                pending_items.append(
                    {
                        "request_id": row.contract_id,
                        "text": f"{row.consumer_did} - {row.dataset_id} (요청일 {row.created_at.date().isoformat()})",
                        "source": "db",
                    }
                )
    for row in IN_MEMORY_ACCESS_REQUESTS:
        if row["status"] == "PENDING":
            pending_items.append(
                {
                    "request_id": row["request_id"],
                    "text": f"{row['requester']} - {row['asset_id']} ({row['usage_scope']}, {row['usage_days']}일)",
                    "source": "memory",
                }
            )
    pending_items = pending_items[:5]
    pending_approvals = len(pending_items)

    max_temp = max((float(row.get("temperature_c", 0.0)) for row in dashboard_rows), default=0.0)
    max_vibration = max((float(row.get("vibration_mm_s", 0.0)) for row in dashboard_rows), default=0.0)
    max_power = max((float(row.get("power_watts", 0.0)) for row in dashboard_rows), default=0.0)
    running_count = len([row for row in dashboard_rows if row.get("status") == "RUNNING"])
    running_ratio = int((running_count / len(dashboard_rows)) * 100) if dashboard_rows else 0

    signal_bars = [
        {"label": "온도(최대)", "value": min(int(max_temp), 100)},
        {"label": "진동(최대)", "value": min(int(max_vibration * 8), 100)},
        {"label": "전력(최대)", "value": min(int((max_power / 600.0) * 100), 100)},
        {"label": "가동률", "value": running_ratio},
    ]

    recent = IN_MEMORY_EVENTS or [
        {"message": "cobot-05 경고 상태 감지 (ALM-TEMP-HIGH)", "time": "방금 전", "type": "경고"},
        {"message": "cobot-08 장애 상태 감지 (ALM-OVERHEAT)", "time": "1분 전", "type": "경고"},
        {"message": "로봇 텔레메트리 샘플 데이터 로드 완료", "time": "3분 전", "type": "등록"},
    ]

    ai_datasets = [asset.get("name") for asset in telemetry_assets][:4]
    if not ai_datasets:
        ai_datasets = ["로봇 텔레메트리 샘플"]

    ai_options = []
    sorted_records = sorted(
        dashboard_rows,
        key=lambda row: str(row.get("produced_at", "")),
    )
    global_chart_series = [
        {
            "time": _to_time_label(row.get("produced_at")),
            "value": min(
                100,
                int(
                    float(row.get("temperature_c", 0.0)) * 0.7
                    + float(row.get("vibration_mm_s", 0.0)) * 4.5
                ),
            ),
        }
        for row in sorted_records
    ]

    for row in dashboard_rows[:10]:
        label = f"{row.get('robot_id', 'robot')} ({row.get('station_id', '-')})"
        alarm_events = [
            {
                "message": alarm,
                "time": _to_time_label(row.get("produced_at")),
            }
            for alarm in row.get("alarms", [])
        ]
        if not alarm_events:
            alarm_events = [{"message": "특이 징후 없음", "time": _to_time_label(row.get("produced_at"))}]
        ai_options.append(
            {
                "label": label,
                "features": {
                    "robot_id": row.get("robot_id"),
                    "station_id": row.get("station_id"),
                    "status": row.get("status"),
                    "temperature_c": row.get("temperature_c", 0),
                    "vibration_mm_s": row.get("vibration_mm_s", 0),
                    "power_watts": row.get("power_watts", 0),
                    "alarms": row.get("alarms", []),
                    "produced_at": row.get("produced_at"),
                    "chart_series": global_chart_series,
                    "alert_events": alarm_events,
                },
            }
        )

    return JSONResponse(
        {
            "kpi": {
                "total_assets": total_assets,
                "telemetry_assets": telemetry_count,
                "pending_approvals": pending_approvals,
                "ai_runs": (telemetry_count * 7 + 12) if telemetry_count else len(sample_records) * 2,
            },
            "sample": {
                "selected": sample,
                "source_file": source_file,
                "available": list(SAMPLE_KEY_TO_FILENAME.keys()),
            },
            "recent_events": recent,
            "signal_bars": signal_bars,
            "pending_items": pending_items,
            "ai_datasets": ai_datasets,
            "ai_options": ai_options,
        }
    )
