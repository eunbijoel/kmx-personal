# AAS Generation Limitations (KMX Manufacturing Pipeline)

## Implemented (structural AAS v3 JSON subset)

- `AssetAdministrationShell` with `assetInformation.assetKind = Instance` and `globalAssetId`
- Submodel references using `ModelReference` and `keys[{type: Submodel}]`
- `Submodel` documents with `id`, `idShort`, `semanticId` (`ExternalReference`), and `submodelElements`
- `Property` elements with `idShort`, `valueType`, and string-serialized `value`

## Not claimed / not implemented

- Full IEC 63278 / IDTA template certification
- Official IDTA semantic IDs for every industry submodel (local `https://kmx.local/...` placeholders are used when no template ID exists)
- `ConceptDescription` and `AssetAdministrationShellDescriptor` registry documents
- AASX package export
- BaSyx / AAS registry push
- Deep validation with `aas-core` (the optional package may be installed, but it is not wired)
- Non-standard `_meta` inside AAS JSON is **rejected** by the structural validator; pipeline metadata is stored separately in `pipeline_result.json`

## Mapping sources

1. `data/control_plane/mappings/aas_mapping.json` (preferred when `dataset_id` matches)
2. Auto-select the mapping with the highest field overlap
3. Fallback: `field_dictionary.json` → `semantic_concept`
