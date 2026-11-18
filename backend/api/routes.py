import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal, get_db
from db.models import AIModel, ConnectorRegistry, DID, DataContract, DatasetMetadata, ODRLPolicy, TransferLog, VerifiableCredential

router = APIRouter()
OLLAMA_GENERATE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:12b")

MODEL_CATALOG = {
    "predictive-maintenance": "Predict machine failure risk from telemetry.",
    "quality-inspection": "Classify product quality anomalies.",
    "process-optimization": "Suggest process parameter optimization.",
    "demand-forecast": "Forecast manufacturing demand and production plan.",
    "energy-optimization": "Optimize energy usage in facility operation.",
}


class MetadataRequest(BaseModel):
    record: dict[str, Any] | list[dict[str, Any]]


class PredictRequest(BaseModel):
    features: dict[str, Any] | list[dict[str, Any]]


def _as_single_record(payload: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            raise HTTPException(400, "record list is empty")
        return payload[0]
    return payload


def _utc_now() -> datetime:
    return datetime.utcnow()


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _extract_metadata(record: dict[str, Any]) -> dict[str, Any]:
    fields = list(record.keys())
    return {
        "fields": fields,
        "field_count": len(fields),
        "sample": record,
    }


def _map_to_ontology(metadata: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "temperature": "mx:MachineTemperature",
        "vibration": "mx:MachineVibration",
        "defect_rate": "mx:DefectRate",
        "energy_kwh": "mx:EnergyConsumption",
        "demand_forecast": "mx:DemandForecast",
    }
    return {
        "mapped_concepts": {
            field: mapping.get(field, "mx:UnknownConcept") for field in metadata.get("fields", [])
        }
    }


def _rule_based_inference(features: dict[str, Any]) -> dict[str, Any]:
    temp = float(features.get("temperature_c", features.get("temperature", 0.0)) or 0.0)
    vibration = float(features.get("vibration_mm_s", features.get("vibration", 0.0)) or 0.0)
    power = float(features.get("power_watts", features.get("power", 0.0)) or 0.0)
    status = str(features.get("status", "")).upper()
    alarms = features.get("alarms", [])
    if not isinstance(alarms, list):
        alarms = []

    score = 0.05
    causes: list[str] = []

    if temp >= 85:
        score += 0.45
        causes.append(f"고온({temp:.1f}C)")
    elif temp >= 70:
        score += 0.28
        causes.append(f"온도 상승({temp:.1f}C)")
    elif temp >= 60:
        score += 0.15
        causes.append(f"온도 주의({temp:.1f}C)")

    if vibration >= 10:
        score += 0.35
        causes.append(f"고진동({vibration:.1f}mm/s)")
    elif vibration >= 4:
        score += 0.2
        causes.append(f"진동 상승({vibration:.1f}mm/s)")
    elif vibration >= 3:
        score += 0.1
        causes.append(f"진동 주의({vibration:.1f}mm/s)")

    if status == "FAULT":
        score += 0.25
        causes.append("장애 상태(FAULT)")
    elif status == "WARNING":
        score += 0.12
        causes.append("경고 상태(WARNING)")

    if alarms:
        score += min(0.3, 0.1 * len(alarms))
        causes.append(f"알람 {len(alarms)}건")

    score = max(0.0, min(score, 1.0))
    recommendation = "intervene" if score >= 0.6 else "monitor"
    reason = ", ".join(causes) if causes else "특이 징후 없음"
    if score >= 0.8:
        alert_level = "critical"
    elif score >= 0.6:
        alert_level = "high"
    elif score >= 0.35:
        alert_level = "medium"
    else:
        alert_level = "low"

    actions: list[str] = []
    if temp >= 70:
        actions.append("냉각 라인과 주변 환기 상태를 먼저 점검하세요.")
    if vibration >= 4:
        actions.append("베어링/축 정렬 상태와 체결 상태를 확인하세요.")
    if alarms:
        actions.append("활성 알람 코드를 우선 순위대로 처리하세요.")
    if status in {"WARNING", "FAULT"}:
        actions.append("현재 설비 상태 이력을 확인해 재발 여부를 점검하세요.")
    if not actions:
        actions.append("현재는 경미한 수준이므로 주기 모니터링을 유지하세요.")

    telemetry_summary = {
        "temperature_c": round(temp, 2),
        "vibration_mm_s": round(vibration, 2),
        "power_watts": round(power, 2),
        "status": status or "UNKNOWN",
        "alarm_count": len(alarms),
    }
    return {
        "score": round(score, 4),
        "recommendation": recommendation,
        "reason": reason,
        "causes": causes,
        "alert_level": alert_level,
        "actions": actions,
        "telemetry_summary": telemetry_summary,
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "platform": "KMX Manufacturing-X Reference"}


@router.get("/metadata")
def ai_metadata() -> dict[str, Any]:
    return {"models": MODEL_CATALOG, "standard_api": ["/predict", "/metadata", "/health"]}


@router.post("/identity/did")
async def create_identity(
    subject_hint: str = "entity",
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    did_value = f"did:kmx:{subject_hint}:{uuid.uuid4().hex[:12]}"
    did = DID(
        did=did_value,
        controller=subject_hint,
        public_key="ed25519-public-placeholder",
        private_key_enc="encrypted-private-placeholder",
        entity_type="human",
    )
    db.add(did)
    await db.flush()
    return {"did": did_value}


@router.post("/identity/vc")
async def create_vc(
    issuer_did: str,
    subject_did: str,
    claims: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    vc_payload = {
        "@context": ["https://www.w3.org/2018/credentials/v1"],
        "type": ["VerifiableCredential", "KmxAccessCredential"],
        "issuer": issuer_did,
        "credentialSubject": {"id": subject_did, **claims},
    }
    signature = _sha256_text(json.dumps(vc_payload, sort_keys=True) + issuer_did)
    vc_id = f"vc:kmx:{uuid.uuid4().hex[:12]}"
    row = VerifiableCredential(
        vc_id=vc_id,
        issuer_did=issuer_did,
        subject_did=subject_did,
        vc_type="KmxAccessCredential",
        claims=claims,
        signature=signature,
        issued_at=_utc_now(),
    )
    db.add(row)
    await db.flush()
    return {"vc_id": vc_id, "vc": {**vc_payload, "proof": {"proofValue": signature}}}


@router.post("/identity/verify-vc")
def check_vc(vc: dict[str, Any]) -> dict[str, bool]:
    proof = vc.get("proof", {}).get("proofValue", "")
    payload = {k: v for k, v in vc.items() if k != "proof"}
    expected = _sha256_text(json.dumps(payload, sort_keys=True) + vc.get("issuer", ""))
    return {"valid": proof == expected}


@router.post("/identity/delegate")
def delegate(user_did: str, agent_did: str, scopes: list[str]) -> dict[str, str]:
    token_payload = {
        "user_did": user_did,
        "agent_did": agent_did,
        "scopes": scopes,
        "issued_at": _utc_now().isoformat(),
        "exp": (_utc_now() + timedelta(hours=1)).isoformat(),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(token_payload).encode("utf-8")).decode("utf-8")
    return {"delegation_token": encoded}


@router.post("/policy/evaluate")
def evaluate_policy(policy: dict[str, Any], action: str, context: dict[str, Any]) -> dict[str, Any]:
    prohibited_actions = {p.get("action") for p in policy.get("prohibitions", [])}
    if action in prohibited_actions:
        return {"allowed": False, "reason": "action is prohibited"}
    allowed_actions = {p.get("action") for p in policy.get("permissions", [])}
    if action not in allowed_actions:
        return {"allowed": False, "reason": "action is not permitted"}
    return {"allowed": True, "reason": "policy matched", "context": context}


@router.post("/contract/create")
async def create_data_contract(
    provider_did: str,
    consumer_did: str,
    asset_id: str,
    policy: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    policy_id = f"pol-{uuid.uuid4().hex[:12]}"
    policy_row = ODRLPolicy(
        policy_id=policy_id,
        title=policy.get("title", "Auto policy"),
        policy_type=policy.get("policy_type", "Agreement"),
        target=asset_id,
        assigner=provider_did,
        assignee=consumer_did,
        permissions=policy.get("permissions", []),
        prohibitions=policy.get("prohibitions", []),
        obligations=policy.get("obligations", []),
    )
    db.add(policy_row)

    contract_id = f"ctr-{uuid.uuid4().hex[:12]}"
    provider_signature = _sha256_text(f"{contract_id}::{provider_did}")
    row = DataContract(
        contract_id=contract_id,
        provider_did=provider_did,
        consumer_did=consumer_did,
        dataset_id=asset_id,
        policy_id=policy_id,
        status="PENDING",
        start_date=_utc_now(),
        terms=policy,
        provider_signature=provider_signature,
    )
    db.add(row)
    await db.flush()
    return {
        "contract_id": contract_id,
        "provider_did": provider_did,
        "consumer_did": consumer_did,
        "asset_id": asset_id,
        "policy_id": policy_id,
        "signature": provider_signature,
        "status": "PENDING",
    }


@router.get("/contract/{contract_id}")
async def get_data_contract(contract_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = await db.scalar(select(DataContract).where(DataContract.contract_id == contract_id))
    if not row:
        raise HTTPException(404, "contract not found")
    return {
        "contract_id": row.contract_id,
        "provider_did": row.provider_did,
        "consumer_did": row.consumer_did,
        "asset_id": row.dataset_id,
        "policy_id": row.policy_id,
        "status": row.status,
        "signature": row.provider_signature,
    }


@router.post("/contract/verify")
def validate_contract(contract: dict[str, Any]) -> dict[str, bool]:
    expected = _sha256_text(f"{contract.get('contract_id')}::{contract.get('provider_did')}")
    return {"valid": contract.get("signature") == expected}


@router.post("/connector/control/register")
async def register_asset(
    asset_id: str,
    owner_did: str,
    endpoint: str,
    metadata: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = DatasetMetadata(
        dataset_id=asset_id,
        title=metadata.get("title", asset_id),
        description=metadata.get("description"),
        owner_did=owner_did,
        data_type=metadata.get("data_type", "JSON"),
        columns=metadata.get("fields", []),
        format=metadata.get("format", "JSON"),
        keywords=metadata.get("keywords", []),
        ontology_mappings=metadata.get("ontology", {}),
        dcat_metadata=metadata,
        access_url=endpoint,
    )
    db.add(row)
    await db.flush()
    return {
        "asset_id": asset_id,
        "owner_did": owner_did,
        "endpoint": endpoint,
        "metadata": metadata,
    }


@router.get("/connector/control/asset/{asset_id}")
async def read_asset(asset_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = await db.scalar(select(DatasetMetadata).where(DatasetMetadata.dataset_id == asset_id))
    if not row:
        raise HTTPException(404, "asset not found")
    return {
        "asset_id": row.dataset_id,
        "owner_did": row.owner_did,
        "endpoint": row.access_url,
        "metadata": row.dcat_metadata,
    }


@router.post("/connector/data/transfer")
async def transfer_data(
    contract_id: str,
    asset_id: str,
    provider_connector: str,
    consumer_connector: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    transfer_id = "tx-" + uuid.uuid4().hex[:14]
    serialized = json.dumps(payload, sort_keys=True)
    digest = _sha256_text(serialized)
    previous_hash = await db.scalar(
        select(TransferLog.current_hash).order_by(TransferLog.timestamp.desc()).limit(1)
    )
    chained_hash = _sha256_text(f"{previous_hash or 'genesis'}::{digest}")

    row = TransferLog(
        transfer_id=transfer_id,
        contract_id=contract_id,
        provider_did=provider_connector,
        consumer_did=consumer_connector,
        dataset_id=asset_id,
        bytes_transferred=len(serialized.encode("utf-8")),
        status="SUCCESS",
        timestamp=_utc_now(),
        prev_hash=previous_hash,
        current_hash=chained_hash,
        log_metadata={"payload_hash": digest},
    )
    db.add(row)
    await db.flush()
    return {
        "transfer_id": transfer_id,
        "contract_id": contract_id,
        "asset_id": asset_id,
        "provider_connector": provider_connector,
        "consumer_connector": consumer_connector,
        "bytes_transferred": row.bytes_transferred,
        "timestamp": row.timestamp.isoformat(),
        "hash": chained_hash,
    }


@router.post("/clearinghouse/settle")
async def clearing_settle(
    contract_id: str,
    unit_price: float = 0.5,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    units = await db.scalar(
        select(func.count()).select_from(TransferLog).where(TransferLog.contract_id == contract_id)
    )
    usage_count = int(units or 0)
    return {
        "id": "set-" + uuid.uuid4().hex[:12],
        "contract_id": contract_id,
        "unit_price": unit_price,
        "units": usage_count,
        "total": usage_count * unit_price,
        "settled_at": _utc_now().isoformat(),
    }


@router.post("/metadata/extract")
def metadata_extract(req: MetadataRequest) -> dict[str, Any]:
    return _extract_metadata(_as_single_record(req.record))


@router.post("/semantic/map")
def semantic_map(req: MetadataRequest) -> dict[str, Any]:
    md = _extract_metadata(_as_single_record(req.record))
    return _map_to_ontology(md)


@router.post("/semantic/index")
async def semantic_index(
    content: str,
    meta: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    doc_id = "doc-" + uuid.uuid4().hex[:12]
    row = DatasetMetadata(
        dataset_id=doc_id,
        title=meta.get("title", doc_id),
        description=content,
        owner_did=meta.get("owner_did", "did:kmx:unknown:system"),
        data_type=meta.get("data_type", "JSON"),
        columns=meta.get("fields", []),
        keywords=meta.get("keywords", []),
        ontology_mappings=meta.get("ontology", {}),
        dcat_metadata=meta,
        access_url=meta.get("access_url"),
    )
    db.add(row)
    await db.flush()
    return {"status": "upserted", "doc_id": doc_id, "storage": "database"}


@router.get("/semantic/search")
async def semantic_search(
    query: str,
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    q = f"%{query}%"
    rows = await db.scalars(
        select(DatasetMetadata)
        .where(
            or_(
                DatasetMetadata.title.ilike(q),
                DatasetMetadata.description.ilike(q),
            )
        )
        .limit(limit)
    )
    items = list(rows)
    return {
        "ids": [row.dataset_id for row in items],
        "documents": [row.description or row.title for row in items],
        "metadatas": [row.dcat_metadata for row in items],
        "storage": "database",
    }


@router.post("/ai/{model_name}/predict")
async def model_predict(
    model_name: str,
    req: PredictRequest,
    require_llm: bool = False,
) -> dict[str, Any]:
    if model_name not in MODEL_CATALOG:
        raise HTTPException(404, "model not supported")
    features = _as_single_record(req.features)
    rule_result = _rule_based_inference(features)

    prompt = (
        "You are a manufacturing AI analyst. "
        "Given telemetry features, return strict JSON with keys: score (0~1), "
        "recommendation (monitor|intervene), reason (short Korean sentence), causes (string array). "
        f"ModelType={model_name}, Features={json.dumps(features, ensure_ascii=False)}, "
        f"BaselineRule={json.dumps(rule_result, ensure_ascii=False)}"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                OLLAMA_GENERATE_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
            )
            response.raise_for_status()
            llm_payload = response.json()
            raw = llm_payload.get("response", "{}")
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            score = float(parsed.get("score", 0.0))
            recommendation = str(parsed.get("recommendation", "monitor"))
            reason = str(parsed.get("reason", "LLM 분석 결과"))
            causes = parsed.get("causes", [])
            if not isinstance(causes, list):
                causes = rule_result["causes"]
            if not causes:
                causes = rule_result["causes"]
            cause_sources = parsed.get("cause_sources")
            if (
                isinstance(cause_sources, list)
                and len(cause_sources) == len(causes)
                and all(str(src).upper() in {"LLM", "RULE"} for src in cause_sources)
            ):
                cause_sources = [str(src).upper() for src in cause_sources]
            elif isinstance(parsed.get("causes"), list) and parsed.get("causes"):
                cause_sources = ["LLM"] * len(causes)
            else:
                cause_sources = ["RULE"] * len(causes)

            if reason.strip() in {"", "LLM 분석 결과"}:
                reason = rule_result["reason"]

            normalized_score = round(max(0.0, min(score, 1.0)), 4)
            if normalized_score == 0.0 and not parsed.get("score"):
                normalized_score = rule_result["score"]

            alert_level = parsed.get("alert_level")
            if alert_level not in {"low", "medium", "high", "critical"}:
                alert_level = rule_result["alert_level"]

            actions = parsed.get("actions", rule_result["actions"])
            if not isinstance(actions, list) or not actions:
                actions = rule_result["actions"]
            action_sources = parsed.get("action_sources")
            if (
                isinstance(action_sources, list)
                and len(action_sources) == len(actions)
                and all(str(src).upper() in {"LLM", "RULE"} for src in action_sources)
            ):
                action_sources = [str(src).upper() for src in action_sources]
            elif isinstance(parsed.get("actions"), list) and parsed.get("actions"):
                action_sources = ["LLM"] * len(actions)
            else:
                action_sources = ["RULE"] * len(actions)

            if AsyncSessionLocal is not None:
                async with AsyncSessionLocal() as session:
                    existing = await session.scalar(select(AIModel).where(AIModel.model_type == model_name))
                    if not existing:
                        session.add(
                            AIModel(
                                model_id=f"mdl-{uuid.uuid4().hex[:10]}",
                                name=model_name,
                                model_type=model_name,
                                description=MODEL_CATALOG[model_name],
                                status="READY",
                            )
                        )
                    await session.commit()

            return {
                "model": model_name,
                "inference": {
                    "score": normalized_score,
                    "recommendation": recommendation,
                    "reason": reason,
                    "causes": causes,
                    "cause_sources": cause_sources,
                    "alert_level": alert_level,
                    "actions": actions,
                    "action_sources": action_sources,
                    "telemetry_summary": rule_result["telemetry_summary"],
                },
                "source": f"ollama:{OLLAMA_MODEL}",
            }
    except Exception:
        if require_llm:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Ollama 모델({OLLAMA_MODEL})에 연결할 수 없습니다. "
                    "ollama serve 실행 및 모델 pull 상태를 확인해 주세요."
                ),
            )
        return {
            "model": model_name,
            "inference": {
                **rule_result,
                "cause_sources": ["RULE"] * len(rule_result.get("causes", [])),
                "action_sources": ["RULE"] * len(rule_result.get("actions", [])),
                "reason": f"{rule_result['reason']} | LLM 연결 실패로 규칙 기반 결과를 반환했습니다.",
            },
            "source": "rule-fallback",
        }


@router.get("/ai/model-status")
async def ai_model_status() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(OLLAMA_GENERATE_URL.replace("/api/generate", "/api/tags"))
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", [])
            loaded = any((row.get("name") or "").startswith(OLLAMA_MODEL) for row in models)
            return {
                "ollama_available": True,
                "model": OLLAMA_MODEL,
                "model_loaded": loaded,
                "available_models": [row.get("name") for row in models if row.get("name")],
            }
    except Exception:
        return {
            "ollama_available": False,
            "model": OLLAMA_MODEL,
            "model_loaded": False,
            "available_models": [],
        }


@router.post("/agentic/auto-onboard")
async def auto_onboard(
    dataset: dict[str, Any] | list[dict[str, Any]],
    owner_did: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    first_record = _as_single_record(dataset)
    md = _extract_metadata(first_record)
    mapped = _map_to_ontology(md)
    asset_id = "asset-" + uuid.uuid4().hex[:10]

    db.add(
        DatasetMetadata(
            dataset_id=asset_id,
            title=md.get("sample", {}).get("title", asset_id),
            description="Auto onboarded dataset",
            owner_did=owner_did,
            data_type="JSON",
            columns=md["fields"],
            keywords=md["fields"],
            ontology_mappings=mapped["mapped_concepts"],
            dcat_metadata={"metadata": md, "ontology": mapped},
            access_url=f"p2p://connector/{asset_id}",
        )
    )
    connector = ConnectorRegistry(
        connector_id=f"conn-{uuid.uuid4().hex[:10]}",
        name=f"connector-{asset_id}",
        owner_did=owner_did,
        endpoint_url=f"p2p://connector/{asset_id}",
    )
    db.add(connector)
    await db.flush()

    return {
        "asset": {
            "asset_id": asset_id,
            "owner_did": owner_did,
            "endpoint": connector.endpoint_url,
        },
        "metadata": md,
        "ontology": mapped,
        "api_draft": {
            "openapi_fragment": {
                "path": f"/connector/data/transfer?asset_id={asset_id}",
                "method": "POST",
                "body_schema": {"type": "object", "example": first_record},
            }
        },
    }
