import logging
from collections import defaultdict
from typing import Dict, Iterator, List, Optional, Set, Tuple

from sqlalchemy import delete, select

from abstract_customer import (
    apply_customer_to_abstract,
    apply_lead_to_abstract,
    get_or_create_abstract_customer,
    merge_abstract_customers,
)
from amocrm import COMPANIES, CONTACTS, Customer, Lead, amocrm_get
from models import AbstractCustomer, CustomerLink, LeadLink, db

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


def _has_delete_in_name(item: dict) -> bool:
    name = ((item.get("general") or {}).get("name")) or ""
    return "удалить" in name.lower()


class SyncIndex:
    def __init__(self) -> None:
        self.customer_by_crm_id: Dict[int, int] = {}
        self.lead_by_crm_id: Dict[int, int] = {}
        self.customer_ids_by_login: Dict[str, Set[int]] = defaultdict(set)
        self.lead_ids_by_login: Dict[str, Set[int]] = defaultdict(set)
        self.abc_cache: Dict[int, AbstractCustomer] = {}

        self.seen_customer_ids: Set[int] = set()
        self.seen_lead_ids: Set[int] = set()
        self.seen_contact_ids_by_abc: Dict[int, Set[int]] = defaultdict(set)
        self.seen_company_ids_by_abc: Dict[int, Set[int]] = defaultdict(set)

    def load_from_db(self) -> None:
        self.customer_by_crm_id.clear()
        self.lead_by_crm_id.clear()
        self.customer_ids_by_login.clear()
        self.lead_ids_by_login.clear()

        for crm_id, abc_id, login in db.session.execute(
            select(CustomerLink.crm_customer_id, CustomerLink.abstract_customer_id, CustomerLink.login)
        ):
            self.customer_by_crm_id[crm_id] = abc_id
            if login:
                self.customer_ids_by_login[login].add(abc_id)

        for crm_id, abc_id, login in db.session.execute(
            select(LeadLink.crm_lead_id, LeadLink.abstract_customer_id, LeadLink.login)
        ):
            self.lead_by_crm_id[crm_id] = abc_id
            if login:
                self.lead_ids_by_login[login].add(abc_id)

    def single_abc_by_lead_login(self, login: str) -> Optional[int]:
        matches = self.lead_ids_by_login.get(login)
        if not matches or len(matches) > 1:
            return None
        return next(iter(matches))

    def single_abc_by_customer_login(self, login: str) -> Optional[int]:
        matches = self.customer_ids_by_login.get(login)
        if not matches or len(matches) > 1:
            return None
        return next(iter(matches))

    def register_customer_link(self, crm_id: int, abc_id: int, login: Optional[str]) -> None:
        old_abc_id = self.customer_by_crm_id.get(crm_id)
        if old_abc_id is not None and old_abc_id != abc_id:
            for ids in self.customer_ids_by_login.values():
                ids.discard(old_abc_id)
        self.customer_by_crm_id[crm_id] = abc_id
        if login:
            self.customer_ids_by_login[login].add(abc_id)

    def register_lead_link(self, crm_id: int, abc_id: int, login: Optional[str]) -> None:
        old_abc_id = self.lead_by_crm_id.get(crm_id)
        if old_abc_id is not None and old_abc_id != abc_id:
            for ids in self.lead_ids_by_login.values():
                ids.discard(old_abc_id)
        self.lead_by_crm_id[crm_id] = abc_id
        if login:
            self.lead_ids_by_login[login].add(abc_id)

    def replace_abc_id(self, old_id: int, new_id: int) -> None:
        for crm_id, abc_id in list(self.customer_by_crm_id.items()):
            if abc_id == old_id:
                self.customer_by_crm_id[crm_id] = new_id
        for crm_id, abc_id in list(self.lead_by_crm_id.items()):
            if abc_id == old_id:
                self.lead_by_crm_id[crm_id] = new_id

        for ids in self.customer_ids_by_login.values():
            if old_id in ids:
                ids.discard(old_id)
                ids.add(new_id)
        for ids in self.lead_ids_by_login.values():
            if old_id in ids:
                ids.discard(old_id)
                ids.add(new_id)

        if old_id in self.seen_contact_ids_by_abc:
            self.seen_contact_ids_by_abc[new_id].update(self.seen_contact_ids_by_abc.pop(old_id))
        if old_id in self.seen_company_ids_by_abc:
            self.seen_company_ids_by_abc[new_id].update(self.seen_company_ids_by_abc.pop(old_id))

        self.abc_cache.pop(old_id, None)


def _upsert_customer_link(crm_id: int, abc_id: int, login: Optional[str], name: Optional[str]) -> None:
    link = db.session.get(CustomerLink, crm_id)
    if link is None:
        db.session.add(
            CustomerLink(
                crm_customer_id=crm_id,
                abstract_customer_id=abc_id,
                login=login,
                name=name,
            )
        )
    else:
        link.abstract_customer_id = abc_id
        link.login = login
        link.name = name


def _upsert_lead_link(crm_id: int, abc_id: int, login: Optional[str], name: Optional[str]) -> None:
    link = db.session.get(LeadLink, crm_id)
    if link is None:
        db.session.add(
            LeadLink(
                crm_lead_id=crm_id,
                abstract_customer_id=abc_id,
                login=login,
                name=name,
            )
        )
    else:
        link.abstract_customer_id = abc_id
        link.login = login
        link.name = name


def process_customer(amo_customer: Customer, idx: SyncIndex, is_full: bool) -> None:
    customer_login = amo_customer.get_field("Логин")
    if not customer_login:
        return

    target_abc_id = idx.customer_by_crm_id.get(amo_customer.id)
    if target_abc_id is None:
        target_abc_id = idx.single_abc_by_lead_login(customer_login)

    abc_customer = get_or_create_abstract_customer(target_abc_id, idx.abc_cache)
    apply_customer_to_abstract(abc_customer, amo_customer)
    _upsert_customer_link(amo_customer.id, abc_customer.id, customer_login, amo_customer.name)
    idx.register_customer_link(amo_customer.id, abc_customer.id, customer_login)

    if is_full:
        idx.seen_customer_ids.add(amo_customer.id)
        idx.seen_contact_ids_by_abc[abc_customer.id].update(
            c["id"] for c in (amo_customer.contacts or []) if c.get("id") is not None
        )
        idx.seen_company_ids_by_abc[abc_customer.id].update(
            c["id"] for c in (amo_customer.companies or []) if c.get("id") is not None
        )


def process_lead(amo_lead: Lead, idx: SyncIndex, is_full: bool) -> None:
    lead_login = amo_lead.get_field("Логин")

    lead_abc_id = idx.lead_by_crm_id.get(amo_lead.id)
    cust_abc_id = idx.single_abc_by_customer_login(lead_login) if lead_login else None

    if lead_abc_id is not None and cust_abc_id is not None and lead_abc_id != cust_abc_id:
        target_abc = get_or_create_abstract_customer(lead_abc_id, idx.abc_cache)
        source_abc = get_or_create_abstract_customer(cust_abc_id, idx.abc_cache)
        merge_abstract_customers(target_abc, source_abc)
        idx.replace_abc_id(old_id=cust_abc_id, new_id=lead_abc_id)
        abc_customer = target_abc
    elif lead_abc_id is not None:
        abc_customer = get_or_create_abstract_customer(lead_abc_id, idx.abc_cache)
    elif cust_abc_id is not None:
        abc_customer = get_or_create_abstract_customer(cust_abc_id, idx.abc_cache)
    else:
        abc_customer = get_or_create_abstract_customer(None, idx.abc_cache)

    apply_lead_to_abstract(abc_customer, amo_lead)
    _upsert_lead_link(amo_lead.id, abc_customer.id, lead_login, amo_lead.name)
    idx.register_lead_link(amo_lead.id, abc_customer.id, lead_login)

    if is_full:
        idx.seen_lead_ids.add(amo_lead.id)
        idx.seen_contact_ids_by_abc[abc_customer.id].update(
            c["id"] for c in (amo_lead.contacts or []) if c.get("id") is not None
        )


def _filter_entities(
    items: Optional[List[dict]],
    allowed_ids: Optional[Set[int]],
) -> Tuple[List[dict], bool]:
    if not items:
        return [], False

    changed = False
    result: List[dict] = []
    for item in items:
        entity_id = (item.get("general") or {}).get("id")
        if _has_delete_in_name(item):
            changed = True
            continue
        if entity_id is None or allowed_ids is None or entity_id not in allowed_ids:
            changed = True
            continue
        result.append(item)
    return result, changed


def cleanup_full_sync(idx: SyncIndex) -> None:
    if idx.seen_customer_ids:
        db.session.execute(
            delete(CustomerLink).where(CustomerLink.crm_customer_id.notin_(idx.seen_customer_ids))
        )
    else:
        db.session.execute(delete(CustomerLink))

    if idx.seen_lead_ids:
        db.session.execute(
            delete(LeadLink).where(LeadLink.crm_lead_id.notin_(idx.seen_lead_ids))
        )
    else:
        db.session.execute(delete(LeadLink))

    batch_count = 0
    for abc_customer in db.session.scalars(select(AbstractCustomer).execution_options(yield_per=BATCH_SIZE)):
        abc_id = abc_customer.id
        any_changed = False

        new_customers, ch = _filter_entities(abc_customer.amo_customers, idx.seen_customer_ids)
        if ch:
            abc_customer.amo_customers = new_customers
            flag_modified(abc_customer, "amo_customers")
            any_changed = True

        new_leads, ch = _filter_entities(abc_customer.amo_leads, idx.seen_lead_ids)
        if ch:
            abc_customer.amo_leads = new_leads
            flag_modified(abc_customer, "amo_leads")
            any_changed = True

        new_contacts, ch = _filter_entities(
            abc_customer.amo_contacts, idx.seen_contact_ids_by_abc.get(abc_id)
        )
        if ch:
            abc_customer.amo_contacts = new_contacts
            flag_modified(abc_customer, "amo_contacts")
            any_changed = True

        new_companies, ch = _filter_entities(
            abc_customer.amo_companies, idx.seen_company_ids_by_abc.get(abc_id)
        )
        if ch:
            abc_customer.amo_companies = new_companies
            flag_modified(abc_customer, "amo_companies")
            any_changed = True

        if not abc_customer.amo_customers and not abc_customer.amo_leads:
            db.session.delete(abc_customer)
            any_changed = True

        if any_changed:
            batch_count += 1
            if batch_count >= BATCH_SIZE:
                db.session.commit()
                db.session.expire_all()
                batch_count = 0

    db.session.commit()
    db.session.expire_all()


def collect_leads_customers(is_full: bool = True) -> None:
    idx = SyncIndex()
    idx.load_from_db()

    customers_ok = False
    leads_ok = False

    try:
        customers: Iterator[Customer] = amocrm_get(Customer, _with=f"{CONTACTS},{COMPANIES}")
        logger.info("Collecting customers (%s)", f"{is_full=}")
        in_batch = 0

        for customer in customers:
            if is_full is False and customer.status_id == 111349:
                continue
            process_customer(customer, idx, is_full=is_full)
            in_batch += 1
            if in_batch >= BATCH_SIZE:
                db.session.commit()
                idx.abc_cache.clear()
                db.session.expire_all()
                in_batch = 0

        db.session.commit()
        idx.abc_cache.clear()
        db.session.expire_all()
        customers_ok = True
    except Exception:
        db.session.rollback()
        logger.exception("Customer sync failed; skipping cleanup")
        return

    try:
        amo_leads: Iterator[Lead] = amocrm_get(Lead, _with=f"{CONTACTS}")
        logger.info("Collecting leads (%s)", f"{is_full=}")
        in_batch = 0

        for amo_lead in amo_leads:
            if is_full is False and amo_lead.status_id == 143:
                continue
            process_lead(amo_lead, idx, is_full=is_full)
            in_batch += 1
            if in_batch >= BATCH_SIZE:
                db.session.commit()
                idx.abc_cache.clear()
                db.session.expire_all()
                in_batch = 0

        db.session.commit()
        idx.abc_cache.clear()
        db.session.expire_all()
        leads_ok = True
    except Exception:
        db.session.rollback()
        logger.exception("Lead sync failed; skipping cleanup")
        return

    if is_full and customers_ok and leads_ok:
        try:
            cleanup_full_sync(idx)
        except Exception:
            db.session.rollback()
            logger.exception("Cleanup failed")