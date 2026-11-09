from __future__ import annotations

from typing import Any


REQUIRED_SHELL_KEYS = {"modelType", "id", "idShort", "assetInformation", "submodels"}
REQUIRED_SUBMODEL_KEYS = {"modelType", "id", "idShort", "submodelElements"}


def validate_aas_documents(shell: dict[str, Any], submodels: list[dict[str, Any]]) -> dict[str, Any]:
    """Structural AAS v3 JSON checks.

    Full schema validation via aas-core is optional and reported when unavailable.
    Non-standard keys such as `_meta` inside AAS documents are rejected.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if shell.get("modelType") != "AssetAdministrationShell":
        errors.append("shell.modelType must be AssetAdministrationShell")
    missing = REQUIRED_SHELL_KEYS - set(shell.keys())
    if missing:
        errors.append(f"shell missing keys: {sorted(missing)}")
    if "_meta" in shell:
        errors.append("shell must not contain non-standard _meta (store meta separately)")

    asset_info = shell.get("assetInformation") or {}
    if asset_info.get("assetKind") not in {"Instance", "Type", "NotApplicable"}:
        errors.append("shell.assetInformation.assetKind invalid")
    if not asset_info.get("globalAssetId"):
        errors.append("shell.assetInformation.globalAssetId required")

    for idx, ref in enumerate(shell.get("submodels") or []):
        if not isinstance(ref, dict):
            errors.append(f"shell.submodels[{idx}] must be object")
            continue
        if ref.get("type") != "ModelReference":
            warnings.append(
                f"shell.submodels[{idx}].type should be ModelReference for AAS v3"
            )
        keys = ref.get("keys") or []
        if not keys or keys[0].get("type") != "Submodel":
            errors.append(f"shell.submodels[{idx}] keys must reference Submodel")

    submodel_ids = set()
    for idx, submodel in enumerate(submodels):
        if submodel.get("modelType") != "Submodel":
            errors.append(f"submodels[{idx}].modelType must be Submodel")
        missing_sm = REQUIRED_SUBMODEL_KEYS - set(submodel.keys())
        if missing_sm:
            errors.append(f"submodels[{idx}] missing keys: {sorted(missing_sm)}")
        if "_meta" in submodel:
            errors.append(f"submodels[{idx}] must not contain non-standard _meta")
        sm_id = submodel.get("id")
        if sm_id in submodel_ids:
            errors.append(f"duplicate submodel id: {sm_id}")
        submodel_ids.add(sm_id)
        for eidx, element in enumerate(submodel.get("submodelElements") or []):
            errors.extend(_validate_element(element, path=f"submodels[{idx}].submodelElements[{eidx}]"))

    referenced = {
        (ref.get("keys") or [{}])[0].get("value")
        for ref in (shell.get("submodels") or [])
        if isinstance(ref, dict)
    }
    for sm_id in submodel_ids:
        if sm_id not in referenced:
            warnings.append(f"submodel {sm_id} not referenced by shell")

    engine = "structural"
    try:
        import importlib.util

        if importlib.util.find_spec("aas_core3") is not None:
            engine = "structural+aas_core3_available_but_not_wired"
            warnings.append("aas_core3 detected but deep validation not wired in this phase")
    except Exception:
        pass

    return {
        "ok": not errors,
        "engine": engine,
        "errors": errors,
        "warnings": warnings,
    }


def _validate_element(element: dict[str, Any], *, path: str) -> list[str]:
    errors: list[str] = []
    model_type = element.get("modelType")
    if model_type not in {"Property", "SubmodelElementCollection"}:
        errors.append(f"{path}.modelType unsupported: {model_type}")
        return errors
    if not element.get("idShort"):
        errors.append(f"{path}.idShort required")
    if model_type == "Property":
        if "valueType" not in element:
            errors.append(f"{path}.valueType required for Property")
        if "value" not in element:
            errors.append(f"{path}.value required for Property")
    else:
        children = element.get("value")
        if not isinstance(children, list):
            errors.append(f"{path}.value must be list for SubmodelElementCollection")
        else:
            for cidx, child in enumerate(children):
                errors.extend(_validate_element(child, path=f"{path}.value[{cidx}]"))
    return errors
