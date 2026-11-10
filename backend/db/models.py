import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def gen_uuid() -> str:
    return str(uuid.uuid4())


class DID(Base):
    __tablename__ = "dids"
    id = Column(String, primary_key=True, default=gen_uuid)
    did = Column(String, unique=True, nullable=False)
    controller = Column(String, nullable=False)
    public_key = Column(Text, nullable=False)
    private_key_enc = Column(Text, nullable=False)
    entity_type = Column(String, default="human")
    created_at = Column(DateTime, default=datetime.utcnow)
    active = Column(Boolean, default=True)
    verifiable_credentials = relationship("VerifiableCredential", back_populates="subject")


class VerifiableCredential(Base):
    __tablename__ = "verifiable_credentials"
    id = Column(String, primary_key=True, default=gen_uuid)
    vc_id = Column(String, unique=True, nullable=False)
    issuer_did = Column(String, nullable=False)
    subject_did = Column(String, ForeignKey("dids.did"), nullable=False)
    vc_type = Column(String, nullable=False)
    claims = Column(JSON, nullable=False)
    issued_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    signature = Column(Text, nullable=False)
    revoked = Column(Boolean, default=False)
    subject = relationship("DID", back_populates="verifiable_credentials")


class ODRLPolicy(Base):
    __tablename__ = "odrl_policies"
    id = Column(String, primary_key=True, default=gen_uuid)
    policy_id = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    policy_type = Column(String, nullable=False)
    target = Column(String, nullable=False)
    assigner = Column(String, nullable=False)
    assignee = Column(String, nullable=True)
    permissions = Column(JSON, default=list)
    prohibitions = Column(JSON, default=list)
    obligations = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    active = Column(Boolean, default=True)


class DataContract(Base):
    __tablename__ = "data_contracts"
    id = Column(String, primary_key=True, default=gen_uuid)
    contract_id = Column(String, unique=True, nullable=False)
    provider_did = Column(String, nullable=False)
    consumer_did = Column(String, nullable=False)
    dataset_id = Column(String, nullable=False)
    policy_id = Column(String, ForeignKey("odrl_policies.policy_id"), nullable=False)
    status = Column(String, default="PENDING")
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=True)
    terms = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    provider_signature = Column(Text, nullable=True)
    consumer_signature = Column(Text, nullable=True)


class DatasetMetadata(Base):
    __tablename__ = "dataset_metadata"
    id = Column(String, primary_key=True, default=gen_uuid)
    dataset_id = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    owner_did = Column(String, nullable=False)
    data_type = Column(String, nullable=False)
    columns = Column(JSON, default=list)
    row_count = Column(Integer, default=0)
    file_size = Column(Integer, default=0)
    format = Column(String, default="CSV")
    keywords = Column(JSON, default=list)
    ontology_mappings = Column(JSON, default=dict)
    dcat_metadata = Column(JSON, default=dict)
    embedding_id = Column(String, nullable=True)
    policy_id = Column(String, nullable=True)
    access_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TransferLog(Base):
    __tablename__ = "transfer_logs"
    id = Column(String, primary_key=True, default=gen_uuid)
    transfer_id = Column(String, unique=True, nullable=False)
    contract_id = Column(String, nullable=False)
    provider_did = Column(String, nullable=False)
    consumer_did = Column(String, nullable=False)
    dataset_id = Column(String, nullable=False)
    bytes_transferred = Column(Integer, default=0)
    status = Column(String, default="SUCCESS")
    timestamp = Column(DateTime, default=datetime.utcnow)
    prev_hash = Column(String, nullable=True)
    current_hash = Column(String, nullable=False)
    log_metadata = Column("metadata", JSON, default=dict)


class AIModel(Base):
    __tablename__ = "ai_models"
    id = Column(String, primary_key=True, default=gen_uuid)
    model_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    model_type = Column(String, nullable=False)
    version = Column(String, default="1.0.0")
    description = Column(Text, nullable=True)
    input_schema = Column(JSON, default=dict)
    output_schema = Column(JSON, default=dict)
    endpoint = Column(String, nullable=True)
    status = Column(String, default="READY")
    created_at = Column(DateTime, default=datetime.utcnow)


class ConnectorRegistry(Base):
    __tablename__ = "connector_registry"
    id = Column(String, primary_key=True, default=gen_uuid)
    connector_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    owner_did = Column(String, nullable=False)
    endpoint_url = Column(String, nullable=False)
    connector_type = Column(String, default="EDC")
    capabilities = Column(JSON, default=list)
    trust_level = Column(String, default="STANDARD")
    registered_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    active = Column(Boolean, default=True)
