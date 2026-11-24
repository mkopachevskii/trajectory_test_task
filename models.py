from enum import Enum

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String, Text, func, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
db = SQLAlchemy(metadata=MetaData(naming_convention=naming_convention))


class AbcCustomerEntities(Enum):
    AmoCustomer = "amo_customers"
    AmoLead = "amo_leads"
    AmoCompany = "amo_companies"
    AmoContact = "amo_contacts"
    SendMethods = "send_methods"


class AbstractCustomer(db.Model):
    __tablename__ = "abstract_customer"

    id = Column(BigInteger(), nullable=False, primary_key=True)
    amo_customers = Column(JSONB)
    amo_leads = Column(JSONB)
    amo_companies = Column(JSONB)
    amo_contacts = Column(JSONB)
    send_methods = Column(JSONB)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    customer_links = relationship(
        "CustomerLink",
        back_populates="abstract_customer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    lead_links = relationship(
        "LeadLink",
        back_populates="abstract_customer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CustomerLink(db.Model):
    __tablename__ = "customer_link"

    crm_customer_id = Column(BigInteger(), primary_key=True)
    abstract_customer_id = Column(
        BigInteger(),
        ForeignKey("abstract_customer.id", ondelete="CASCADE"),
        nullable=False,
    )
    login = Column(String(255), nullable=True)
    name = Column(Text, nullable=True)

    abstract_customer = relationship("AbstractCustomer", back_populates="customer_links")

    __table_args__ = (
        Index("ix_customer_link_abc_id", "abstract_customer_id"),
        Index("ix_customer_link_login", "login"),
        Index("ix_customer_link_name", "name"),
    )


class LeadLink(db.Model):
    __tablename__ = "lead_link"

    crm_lead_id = Column(BigInteger(), primary_key=True)
    abstract_customer_id = Column(
        BigInteger(),
        ForeignKey("abstract_customer.id", ondelete="CASCADE"),
        nullable=False,
    )
    login = Column(String(255), nullable=True)
    name = Column(Text, nullable=True)

    abstract_customer = relationship("AbstractCustomer", back_populates="lead_links")

    __table_args__ = (
        Index("ix_lead_link_abc_id", "abstract_customer_id"),
        Index("ix_lead_link_login", "login"),
        Index("ix_lead_link_name", "name"),
    )