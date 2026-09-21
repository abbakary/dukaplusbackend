"""Extra demo data for one featured tenant (Sinza Hardware) — TRA, accounting, payroll."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.core.secret_box import encrypt_secret
from app.database import AsyncSessionLocal
from app.models import StaffMember, Tenant, TenantSettings, User
from app.models.accounting import HrPayrollContract, HrPayslip, JournalEntry, JournalLine, LedgerAccount
from app.models.tra_efd import FiscalReceiptRecord, TenantTraEfdConfig
from app.services.accounting_defaults import ensure_default_chart

logger = logging.getLogger(__name__)

SINZA_OWNER_EMAIL = "owner.sinza-hardware@sample.dukaplus.co.tz"
DEMO_TRA_CLIENT_ID = "demo-sinza-client-id"
DEMO_TRA_SECRET = "demo-sinza-client-secret"


async def seed_compliance_demo_for_sinza() -> None:
    from app.database import init_db

    await init_db()
    async with AsyncSessionLocal() as db:
        tenant_result = await db.execute(select(Tenant).where(Tenant.owner_email == SINZA_OWNER_EMAIL))
        tenant = tenant_result.scalar_one_or_none()
        if not tenant:
            logger.info("Sinza sample tenant not found — skip compliance demo seed")
            return

        tenant_id = tenant.id

        tra_result = await db.execute(
            select(TenantTraEfdConfig).where(TenantTraEfdConfig.tenant_id == tenant_id)
        )
        tra = tra_result.scalar_one_or_none()
        if not tra:
            tra = TenantTraEfdConfig(
                tenant_id=tenant_id,
                active=True,
                client_id=DEMO_TRA_CLIENT_ID,
                client_secret_enc=encrypt_secret(DEMO_TRA_SECRET),
                company_city="DAR ES SALAAM",
                company_mobile=tenant.owner_phone or "+255710000003",
                default_id_type="6",
                is_demo=True,
                connection_status="connected",
                last_connection=datetime.now(UTC),
                company_vrn="VRN-DEMO-SINZA-001",
                company_tin=tenant.tin_number or "TIN-100000003",
                company_serial=tenant.tra_efd_serial or "EFD-SINZA-DEMO",
                company_vin="VIN-DEMO-001",
                tax_office="Ilala Tax Office",
                token_user_name="John Mrema",
                token_email=SINZA_OWNER_EMAIL,
            )
            db.add(tra)
        else:
            tra.active = True
            tra.is_demo = True
            if not tra.client_secret_enc:
                tra.client_secret_enc = encrypt_secret(DEMO_TRA_SECRET)
            if not tra.client_id:
                tra.client_id = DEMO_TRA_CLIENT_ID

        settings_result = await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
        )
        settings_row = settings_result.scalar_one_or_none()
        business = {
            "mode": "tra_efd",
            "vatRegistered": True,
            "vatEnabled": True,
            "vatRate": 0.18,
            "pricesIncludeVat": True,
            "showVatOnReceipt": True,
            "showTraSignature": True,
            "traEfdSerial": tra.company_serial,
            "tinNumber": tenant.tin_number,
            "vrnNumber": tra.company_vrn,
            "receiptBusinessName": tenant.name,
            "receiptFooterNote": "Asante kwa kununua — Sinza Hardware & Building",
        }
        if not settings_row:
            db.add(TenantSettings(tenant_id=tenant_id, business_settings=business, document_config={}))
        else:
            merged = {**(settings_row.business_settings or {}), **business}
            settings_row.business_settings = merged

        accounts = await ensure_default_chart(db, tenant_id)
        by_code = {a.code: a for a in accounts}

        existing_je = await db.execute(
            select(JournalEntry.id).where(JournalEntry.tenant_id == tenant_id).limit(1)
        )
        if not existing_je.scalar_one_or_none():
            entry = JournalEntry(
                tenant_id=tenant_id,
                entry_date=date.today() - timedelta(days=3),
                reference="SAMPLE-POS-001",
                memo="Sample POS sales posting (demo)",
                source="pos_sale",
            )
            db.add(entry)
            await db.flush()
            amount = 425_000.0
            cash = by_code.get("1000")
            sales = by_code.get("4000")
            vat = by_code.get("2100")
            if cash and sales and vat:
                net = round(amount / 1.18, 2)
                vat_amt = round(amount - net, 2)
                db.add(
                    JournalLine(
                        entry_id=entry.id,
                        account_id=cash.id,
                        label="Cash from POS",
                        debit=amount,
                        credit=0,
                    )
                )
                db.add(
                    JournalLine(
                        entry_id=entry.id,
                        account_id=sales.id,
                        label="Hardware sales",
                        debit=0,
                        credit=net,
                    )
                )
                db.add(
                    JournalLine(
                        entry_id=entry.id,
                        account_id=vat.id,
                        label="Output VAT",
                        debit=0,
                        credit=vat_amt,
                    )
                )

        staff_result = await db.execute(
            select(StaffMember).where(StaffMember.tenant_id == tenant_id).limit(5)
        )
        staff_rows = staff_result.scalars().all()
        for sm in staff_rows:
            contract_q = await db.execute(
                select(HrPayrollContract).where(
                    HrPayrollContract.tenant_id == tenant_id,
                    HrPayrollContract.staff_id == sm.id,
                )
            )
            if contract_q.scalar_one_or_none():
                continue
            db.add(
                HrPayrollContract(
                    tenant_id=tenant_id,
                    staff_id=sm.id,
                    staff_name=sm.name,
                    wage_monthly=850_000 if sm.role.value == "Owner" else 450_000,
                    structure_code="standard",
                    nssf_enabled=True,
                    paye_enabled=True,
                    active=True,
                )
            )

        period = date.today().strftime("%Y-%m")
        payslip_q = await db.execute(
            select(HrPayslip.id).where(
                HrPayslip.tenant_id == tenant_id,
                HrPayslip.period == period,
            ).limit(1)
        )
        if not payslip_q.scalar_one_or_none() and staff_rows:
            owner = staff_rows[0]
            lines = [
                {"code": "BASIC", "name": "Basic salary", "amount": 850_000},
                {"code": "NSSF", "name": "NSSF employee", "amount": -42_500},
                {"code": "PAYE", "name": "PAYE", "amount": -95_000},
            ]
            db.add(
                HrPayslip(
                    tenant_id=tenant_id,
                    staff_id=owner.id,
                    staff_name=owner.name,
                    period=period,
                    gross_pay=850_000,
                    deductions=137_500,
                    net_pay=712_500,
                    status="draft",
                    payslip_number=f"PS-{period}-SINZA-001",
                    lines_json=json.dumps(lines),
                )
            )

        fiscal_q = await db.execute(
            select(FiscalReceiptRecord.id).where(FiscalReceiptRecord.tenant_id == tenant_id).limit(1)
        )
        if not fiscal_q.scalar_one_or_none():
            db.add(
                FiscalReceiptRecord(
                    tenant_id=tenant_id,
                    invoice_reference="RCP-SAMPLE-SINZA-001",
                    receipt_number="RCP-SAMPLE-SINZA-001",
                    verification_code="DEMO-VERIFY-001",
                    verify_link="https://verify.tra.go.tz/?vrn=VRN-DEMO-SINZA-001&code=DEMO-VERIFY-001",
                    z_number=tra.company_serial,
                    vrn=tra.company_vrn,
                    status="demo",
                    is_demo=True,
                    customer_name="Walk-in Customer",
                    total_excl_tax=360_169.49,
                    total_tax=64_830.51,
                    total_incl_tax=425_000,
                    items_json=[
                        {
                            "itemdesc": "Cement 50kg",
                            "itemqty": 2,
                            "amount": 425_000,
                        }
                    ],
                    api_response_raw='{"status":"demo","message":"Sample fiscal receipt for UI testing"}',
                )
            )

        await db.commit()
        logger.info("Seeded TRA/accounting/payroll demo data for Sinza Hardware (%s)", tenant_id)
