import logging
from collections import defaultdict
from typing import Dict, List, Optional

from sqlalchemy.orm.attributes import flag_modified

from amocrm import Customer, Lead
from models import AbstractCustomer, db

logger = logging.getLogger(__name__)


def dict_compare_merge(l_dict1: List[dict], l_dict2: List[dict]) -> List[dict]:
    d = defaultdict(dict)
    for item in (l_dict1 or []) + (l_dict2 or []):
        general = item.get("general") or {}
        entity_id = general.get("id")
        if entity_id is not None:
            d[entity_id].update(item)
    return list(d.values())


def get_customer_json(customer: Customer) -> dict:
    cf = customer.custom_fields_values or {}
    return {
        "general": {
            "id": customer.id,
            "login": (cf.get("Логин") or {}).get("value"),
            "billing_type": (cf.get("Вид оплаты") or {}).get("value"),
            "is_robot": bool(cf.get("Робот")),
            "is_nds": bool(cf.get("Оплата с НДС")),
            "system": (cf.get("Система") or {}).get("value"),
            "host": (cf.get("Хост") or {}).get("value"),
            "name": customer.name,
        },
        "api_value": customer.as_dict(),
    }


def get_lead_json(lead: Lead) -> dict:
    cf = lead.custom_fields_values or {}
    return {
        "general": {
            "id": lead.id,
            "login": (cf.get("Логин") or {}).get("value"),
            "name": lead.name,
        },
        "api_value": lead.as_dict(),
    }


def upsert_jsonb_item(items: Optional[List[dict]], new_item: dict) -> List[dict]:
    result = list(items or [])
    new_id = (new_item.get("general") or {}).get("id")
    for idx, existing in enumerate(result):
        if (existing.get("general") or {}).get("id") == new_id:
            result[idx] = new_item
            return result
    result.append(new_item)
    return result


def get_or_create_abstract_customer(
    abc_id: Optional[int],
    cache: Dict[int, AbstractCustomer],
) -> AbstractCustomer:
    if abc_id is not None:
        if abc_id in cache:
            return cache[abc_id]
        obj = db.session.get(AbstractCustomer, abc_id)
        if obj is not None:
            cache[abc_id] = obj
            return obj

    new_obj = AbstractCustomer(
        amo_customers=[],
        amo_leads=[],
        amo_companies=[],
        amo_contacts=[],
        send_methods=[],
    )
    db.session.add(new_obj)
    db.session.flush()
    cache[new_obj.id] = new_obj
    return new_obj


def apply_customer_to_abstract(
    abstract_customer: AbstractCustomer,
    amo_customer: Customer,
) -> AbstractCustomer:
    abstract_customer.amo_customers = upsert_jsonb_item(
        abstract_customer.amo_customers, get_customer_json(amo_customer)
    )
    flag_modified(abstract_customer, "amo_customers")
    return abstract_customer


def apply_lead_to_abstract(
    abstract_customer: AbstractCustomer,
    amo_lead: Lead,
) -> AbstractCustomer:
    abstract_customer.amo_leads = upsert_jsonb_item(
        abstract_customer.amo_leads, get_lead_json(amo_lead)
    )
    flag_modified(abstract_customer, "amo_leads")
    return abstract_customer


def merge_abstract_customers(
    target: AbstractCustomer,
    source: AbstractCustomer,
) -> None:
    if target.id == source.id:
        return
    for attr in ("amo_customers", "amo_leads", "amo_contacts", "amo_companies", "send_methods"):
        merged = dict_compare_merge(getattr(target, attr) or [], getattr(source, attr) or [])
        setattr(target, attr, merged)
        flag_modified(target, attr)
    db.session.flush()
    db.session.delete(source)
    db.session.flush()