#!/usr/bin/env python3
"""Seed script - populates the database with realistic mock data for testing.

Usage:
    # Fresh seed (drops and recreates all tables first):
    python seed_data.py

    # Append to existing data (no table drop):
    python seed_data.py --append

    # Custom database URI:
    DATABASE_URI=postgresql://user:pass@host/db python seed_data.py
"""
from __future__ import annotations

import argparse
import datetime
import sys

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    AuditLog,
    Bundle,
    BundleItem,
    Contact,
    DeliveryItem,
    DeliveryItemComponent,
    DeliveryNote,
    DeliveryNoteOrder,
    Invoice,
    InvoiceItem,
    LogisticsPlan,
    Order,
    OrderItem,
    Partner,
    PartnerAddress,
    Product,
    User,
    Vehicle,
    VehicleSchedule,
)


def seed(append: bool = False):
    app = create_app()
    with app.app_context():
        if not append:
            print("Dropping all tables...")
            db.drop_all()
            print("Creating all tables...")
            db.create_all()
        else:
            print("Appending to existing data...")
            db.create_all()

        # ── 1. Users (4 roles) ──────────────────────────────────────────
        print("Creating users...")
        admin = User(
            username="admin",
            password_hash=generate_password_hash("admin123"),
            role="admin",
        )
        operator = User(
            username="operator",
            password_hash=generate_password_hash("operator123"),
            role="operator",
        )
        collector = User(
            username="zberač",
            password_hash=generate_password_hash("collector123"),
            role="collector",
        )
        customer = User(
            username="zakaznik",
            password_hash=generate_password_hash("customer123"),
            role="customer",
        )
        db.session.add_all([admin, operator, collector, customer])
        db.session.flush()

        # ── 2. Partners (Slovak companies) ──────────────────────────────
        print("Creating partners...")
        partner_a = Partner(
            name="ABC Stavby s.r.o.",
            note="Hlavný odberateľ stavebného materiálu",
            street="Hlavná",
            street_number="15",
            postal_code="81101",
            city="Bratislava",
            group_code="STAVBY",
            ico="12345678",
            dic="2012345678",
            ic_dph="SK2012345678",
            email="info@abcstavby.sk",
            phone="+421 2 1234 5678",
            price_level="A",
            discount_percent=5.0,
        )
        partner_b = Partner(
            name="Kovex Trade a.s.",
            note="Dodávateľ kovových výrobkov",
            street="Priemyselná",
            street_number="42",
            postal_code="04001",
            city="Košice",
            group_code="KOVY",
            ico="87654321",
            dic="2087654321",
            ic_dph="SK2087654321",
            email="obchod@kovextrade.sk",
            phone="+421 55 678 9012",
            price_level="B",
            discount_percent=3.0,
        )
        partner_c = Partner(
            name="GreenFood Slovakia s.r.o.",
            note="Distribútor potravín",
            street="Záhradná",
            street_number="7A",
            postal_code="94901",
            city="Nitra",
            group_code="POTRAVINY",
            ico="11223344",
            dic="2011223344",
            ic_dph="SK2011223344",
            email="objednavky@greenfood.sk",
            phone="+421 37 654 3210",
            price_level="A",
            discount_percent=0.0,
        )
        partner_d = Partner(
            name="TechnoServis Plus s.r.o.",
            note="IT služby a servis",
            street="Digitálna",
            street_number="100",
            postal_code="01001",
            city="Žilina",
            group_code="STAVBY",  # same group as partner_a for group testing
            ico="55667788",
            dic="2055667788",
            ic_dph="SK2055667788",
            email="servis@technoplus.sk",
            phone="+421 41 555 1234",
            price_level="C",
            discount_percent=10.0,
        )
        partner_e = Partner(
            name="DrevoPol s.r.o.",
            note="Veľkoobchod s drevom",
            street="Lesná",
            street_number="3",
            postal_code="96001",
            city="Zvolen",
            ico="99887766",
            dic="2099887766",
            email="drevo@drevopol.sk",
            phone="+421 45 111 2233",
            price_level="B",
            discount_percent=2.5,
        )
        db.session.add_all([partner_a, partner_b, partner_c, partner_d, partner_e])
        db.session.flush()

        # ── 3. Partner Addresses ────────────────────────────────────────
        print("Creating addresses...")
        addr_a_pickup = PartnerAddress(
            partner_id=partner_a.id,
            address_type="pickup",
            street="Skladová",
            street_number="8",
            postal_code="82104",
            city="Bratislava",
        )
        addr_a_delivery = PartnerAddress(
            partner_id=partner_a.id,
            address_type="delivery",
            street="Stavbárska",
            street_number="22",
            postal_code="83106",
            city="Bratislava",
        )
        addr_b_pickup = PartnerAddress(
            partner_id=partner_b.id,
            address_type="pickup",
            street="Hutná",
            street_number="1",
            postal_code="04011",
            city="Košice",
        )
        addr_c_delivery = PartnerAddress(
            partner_id=partner_c.id,
            address_type="delivery",
            street="Tržná",
            street_number="5",
            postal_code="94911",
            city="Nitra",
        )
        addr_d_pickup = PartnerAddress(
            partner_id=partner_d.id,
            address_type="pickup",
            related_partner_id=partner_a.id,
            street="Hlavná",
            street_number="15",
            postal_code="81101",
            city="Bratislava",
        )
        db.session.add_all([addr_a_pickup, addr_a_delivery, addr_b_pickup, addr_c_delivery, addr_d_pickup])
        db.session.flush()

        # ── 4. Contacts ────────────────────────────────────────────────
        print("Creating contacts...")
        contacts = [
            Contact(partner_id=partner_a.id, name="Ján Novák", email="novak@abcstavby.sk",
                    phone="+421 903 111 222", role="Konateľ", can_order=True, can_receive=True),
            Contact(partner_id=partner_a.id, name="Mária Kováčová", email="kovacova@abcstavby.sk",
                    phone="+421 903 333 444", role="Skladníčka", can_order=False, can_receive=True),
            Contact(partner_id=partner_b.id, name="Peter Horváth", email="horvath@kovextrade.sk",
                    phone="+421 911 555 666", role="Obchodný riaditeľ", can_order=True, can_receive=False),
            Contact(partner_id=partner_c.id, name="Eva Tóthová", email="tothova@greenfood.sk",
                    phone="+421 917 777 888", role="Nákupca", can_order=True, can_receive=True),
            Contact(partner_id=partner_d.id, name="Lukáš Baran", email="baran@technoplus.sk",
                    phone="+421 905 999 000", role="Technik", can_order=True, can_receive=True),
        ]
        db.session.add_all(contacts)
        db.session.flush()

        # ── 5. Products ────────────────────────────────────────────────
        print("Creating products...")
        prod_cement = Product(name="Cement CEM I 42.5", description="Portlandský cement 25kg",
                              price=6.50, is_service=False, discount_excluded=False)
        prod_tehla = Product(name="Tehla plná pálená", description="Tehla CP 290x140x65mm",
                             price=0.45, is_service=False, discount_excluded=False)
        prod_piesok = Product(name="Štrk frakcia 4-8mm", description="Riečny štrk, 1 tona",
                              price=28.00, is_service=False, discount_excluded=False)
        prod_doprava = Product(name="Doprava do 50km", description="Doprava nákladným vozidlom",
                               price=45.00, is_service=True, discount_excluded=True)
        prod_nakladka = Product(name="Nakládka materiálu", description="Nakládka pomocou žeriavu",
                                price=25.00, is_service=True, discount_excluded=True)
        prod_konzultacia = Product(name="Stavebná konzultácia", description="Odborné poradenstvo, 1h",
                                   price=60.00, is_service=True, discount_excluded=False)
        prod_izolacia = Product(name="Tepelná izolácia EPS 100", description="Polystyrén 100mm, 1m2",
                                price=8.20, is_service=False, discount_excluded=False)
        prod_oceľ = Product(name="Betonárska oceľ B500B", description="Prúty 12mm, 1 tona",
                            price=850.00, is_service=False, discount_excluded=False)
        products = [prod_cement, prod_tehla, prod_piesok, prod_doprava,
                    prod_nakladka, prod_konzultacia, prod_izolacia, prod_oceľ]
        db.session.add_all(products)
        db.session.flush()

        # ── 6. Bundles ─────────────────────────────────────────────────
        print("Creating bundles...")
        bundle_zaklad = Bundle(name="Základový balík", bundle_price=120.00)
        bundle_murivo = Bundle(name="Murovací set", bundle_price=35.00)
        db.session.add_all([bundle_zaklad, bundle_murivo])
        db.session.flush()

        bundle_items = [
            BundleItem(bundle_id=bundle_zaklad.id, product_id=prod_cement.id, quantity=10),
            BundleItem(bundle_id=bundle_zaklad.id, product_id=prod_piesok.id, quantity=2),
            BundleItem(bundle_id=bundle_zaklad.id, product_id=prod_oceľ.id, quantity=1),
            BundleItem(bundle_id=bundle_murivo.id, product_id=prod_tehla.id, quantity=50),
            BundleItem(bundle_id=bundle_murivo.id, product_id=prod_cement.id, quantity=5),
        ]
        db.session.add_all(bundle_items)
        db.session.flush()

        # ── 7. Orders ──────────────────────────────────────────────────
        print("Creating orders...")
        now = datetime.datetime.utcnow()
        order1 = Order(
            partner_id=partner_a.id,
            pickup_address_id=addr_a_pickup.id,
            delivery_address_id=addr_a_delivery.id,
            created_by_id=admin.id,
            pickup_datetime=now + datetime.timedelta(days=1, hours=8),
            delivery_datetime=now + datetime.timedelta(days=1, hours=14),
            pickup_method="Vlastný odvoz",
            delivery_method="Nákladné auto",
            payment_method="Faktúra",
            payment_terms="30 dní",
            show_prices=True,
            confirmed=True,
        )
        order2 = Order(
            partner_id=partner_b.id,
            pickup_address_id=addr_b_pickup.id,
            created_by_id=operator.id,
            pickup_datetime=now + datetime.timedelta(days=2, hours=9),
            delivery_datetime=now + datetime.timedelta(days=3, hours=10),
            pickup_method="Zvoz",
            delivery_method="Kuriér",
            payment_method="Dobierka",
            payment_terms="Na dodanie",
            show_prices=True,
            confirmed=True,
        )
        order3 = Order(
            partner_id=partner_c.id,
            delivery_address_id=addr_c_delivery.id,
            created_by_id=operator.id,
            pickup_datetime=now + datetime.timedelta(days=5),
            delivery_datetime=now + datetime.timedelta(days=6),
            pickup_method="Vlastný odvoz",
            delivery_method="Dodávka",
            payment_method="Prevodom",
            payment_terms="14 dní",
            show_prices=False,
            confirmed=False,
        )
        order4 = Order(
            partner_id=partner_d.id,
            pickup_address_id=addr_d_pickup.id,
            created_by_id=admin.id,
            pickup_datetime=now - datetime.timedelta(days=3),
            delivery_datetime=now - datetime.timedelta(days=2),
            pickup_method="Zvoz",
            delivery_method="Vlastný odvoz",
            payment_method="Faktúra",
            payment_terms="60 dní",
            show_prices=True,
            confirmed=True,
        )
        order5 = Order(
            partner_id=partner_a.id,
            pickup_address_id=addr_a_pickup.id,
            delivery_address_id=addr_a_delivery.id,
            created_by_id=operator.id,
            pickup_datetime=now - datetime.timedelta(days=10),
            delivery_datetime=now - datetime.timedelta(days=9),
            pickup_method="Zvoz",
            delivery_method="Nákladné auto",
            payment_method="Faktúra",
            payment_terms="30 dní",
            show_prices=True,
            confirmed=True,
        )
        db.session.add_all([order1, order2, order3, order4, order5])
        db.session.flush()

        # ── 8. Order Items ─────────────────────────────────────────────
        print("Creating order items...")
        order_items = [
            # Order 1: cement + bricks + transport
            OrderItem(order_id=order1.id, product_id=prod_cement.id, quantity=20, unit_price=6.50),
            OrderItem(order_id=order1.id, product_id=prod_tehla.id, quantity=500, unit_price=0.45),
            OrderItem(order_id=order1.id, product_id=prod_doprava.id, quantity=1, unit_price=45.00),
            # Order 2: steel + gravel
            OrderItem(order_id=order2.id, product_id=prod_oceľ.id, quantity=2, unit_price=850.00),
            OrderItem(order_id=order2.id, product_id=prod_piesok.id, quantity=5, unit_price=28.00),
            # Order 3: insulation + consultation
            OrderItem(order_id=order3.id, product_id=prod_izolacia.id, quantity=100, unit_price=8.20),
            OrderItem(order_id=order3.id, product_id=prod_konzultacia.id, quantity=3, unit_price=60.00),
            # Order 4: cement + loading service
            OrderItem(order_id=order4.id, product_id=prod_cement.id, quantity=50, unit_price=6.50),
            OrderItem(order_id=order4.id, product_id=prod_nakladka.id, quantity=2, unit_price=25.00),
            # Order 5: past order - bricks + transport
            OrderItem(order_id=order5.id, product_id=prod_tehla.id, quantity=1000, unit_price=0.45),
            OrderItem(order_id=order5.id, product_id=prod_doprava.id, quantity=2, unit_price=45.00),
        ]
        db.session.add_all(order_items)
        db.session.flush()

        # ── 9. Delivery Notes ──────────────────────────────────────────
        print("Creating delivery notes...")
        dn1 = DeliveryNote(
            primary_order_id=order1.id,
            created_by_id=admin.id,
            show_prices=True,
            planned_delivery_datetime=now + datetime.timedelta(days=1, hours=14),
            confirmed=False,
        )
        dn2 = DeliveryNote(
            primary_order_id=order4.id,
            created_by_id=operator.id,
            show_prices=True,
            planned_delivery_datetime=now - datetime.timedelta(days=2),
            actual_delivery_datetime=now - datetime.timedelta(days=2, hours=-3),
            confirmed=True,
        )
        dn3 = DeliveryNote(
            primary_order_id=order5.id,
            created_by_id=operator.id,
            show_prices=True,
            planned_delivery_datetime=now - datetime.timedelta(days=9),
            actual_delivery_datetime=now - datetime.timedelta(days=9, hours=-5),
            confirmed=True,
        )
        db.session.add_all([dn1, dn2, dn3])
        db.session.flush()

        # Link delivery notes to orders
        db.session.add_all([
            DeliveryNoteOrder(delivery_note_id=dn1.id, order_id=order1.id),
            DeliveryNoteOrder(delivery_note_id=dn2.id, order_id=order4.id),
            DeliveryNoteOrder(delivery_note_id=dn3.id, order_id=order5.id),
        ])

        # Delivery items for dn1 (from order1)
        di1 = DeliveryItem(delivery_note_id=dn1.id, product_id=prod_cement.id,
                           quantity=20, unit_price=6.50, line_total=130.00)
        di2 = DeliveryItem(delivery_note_id=dn1.id, product_id=prod_tehla.id,
                           quantity=500, unit_price=0.45, line_total=225.00)
        di3 = DeliveryItem(delivery_note_id=dn1.id, product_id=prod_doprava.id,
                           quantity=1, unit_price=45.00, line_total=45.00)
        # Delivery items for dn2 (from order4)
        di4 = DeliveryItem(delivery_note_id=dn2.id, product_id=prod_cement.id,
                           quantity=50, unit_price=6.50, line_total=325.00)
        di5 = DeliveryItem(delivery_note_id=dn2.id, product_id=prod_nakladka.id,
                           quantity=2, unit_price=25.00, line_total=50.00)
        # Delivery items for dn3 (from order5) - includes a bundle
        di6 = DeliveryItem(delivery_note_id=dn3.id, product_id=prod_tehla.id,
                           quantity=1000, unit_price=0.45, line_total=450.00)
        di7 = DeliveryItem(delivery_note_id=dn3.id, product_id=prod_doprava.id,
                           quantity=2, unit_price=45.00, line_total=90.00)
        di8 = DeliveryItem(delivery_note_id=dn3.id, bundle_id=bundle_murivo.id,
                           quantity=2, unit_price=35.00, line_total=70.00)
        db.session.add_all([di1, di2, di3, di4, di5, di6, di7, di8])
        db.session.flush()

        # Bundle components for di8
        db.session.add_all([
            DeliveryItemComponent(delivery_item_id=di8.id, product_id=prod_tehla.id, quantity=100),
            DeliveryItemComponent(delivery_item_id=di8.id, product_id=prod_cement.id, quantity=10),
        ])

        # Mark dn3 as invoiced (for invoice test)
        dn3.invoiced = True

        # ── 10. Vehicles ───────────────────────────────────────────────
        print("Creating vehicles...")
        v1 = Vehicle(name="MAN TGS 26.400 (BA-123AB)", notes="Nákladné auto 26t", active=True)
        v2 = Vehicle(name="Iveco Daily (KE-456CD)", notes="Dodávka 3.5t", active=True)
        v3 = Vehicle(name="DAF XF (ZA-789EF)", notes="Ťahač s návesom - v oprave", active=False)
        db.session.add_all([v1, v2, v3])
        db.session.flush()

        # ── 11. Vehicle Schedules ──────────────────────────────────────
        print("Creating vehicle schedules...")
        schedules = [
            # v1: Mon-Fri 6:00-18:00
            VehicleSchedule(vehicle_id=v1.id, day_of_week=0, start_time=datetime.time(6, 0), end_time=datetime.time(18, 0)),
            VehicleSchedule(vehicle_id=v1.id, day_of_week=1, start_time=datetime.time(6, 0), end_time=datetime.time(18, 0)),
            VehicleSchedule(vehicle_id=v1.id, day_of_week=2, start_time=datetime.time(6, 0), end_time=datetime.time(18, 0)),
            VehicleSchedule(vehicle_id=v1.id, day_of_week=3, start_time=datetime.time(6, 0), end_time=datetime.time(18, 0)),
            VehicleSchedule(vehicle_id=v1.id, day_of_week=4, start_time=datetime.time(6, 0), end_time=datetime.time(18, 0)),
            # v2: Mon-Sat 7:00-15:00
            VehicleSchedule(vehicle_id=v2.id, day_of_week=0, start_time=datetime.time(7, 0), end_time=datetime.time(15, 0)),
            VehicleSchedule(vehicle_id=v2.id, day_of_week=1, start_time=datetime.time(7, 0), end_time=datetime.time(15, 0)),
            VehicleSchedule(vehicle_id=v2.id, day_of_week=2, start_time=datetime.time(7, 0), end_time=datetime.time(15, 0)),
            VehicleSchedule(vehicle_id=v2.id, day_of_week=3, start_time=datetime.time(7, 0), end_time=datetime.time(15, 0)),
            VehicleSchedule(vehicle_id=v2.id, day_of_week=4, start_time=datetime.time(7, 0), end_time=datetime.time(15, 0)),
            VehicleSchedule(vehicle_id=v2.id, day_of_week=5, start_time=datetime.time(8, 0), end_time=datetime.time(12, 0)),
        ]
        db.session.add_all(schedules)
        db.session.flush()

        # ── 12. Logistics Plans ────────────────────────────────────────
        print("Creating logistics plans...")
        plans = [
            LogisticsPlan(order_id=order1.id, delivery_note_id=dn1.id,
                          plan_type="delivery", planned_datetime=now + datetime.timedelta(days=1, hours=14),
                          vehicle_id=v1.id),
            LogisticsPlan(order_id=order2.id,
                          plan_type="pickup", planned_datetime=now + datetime.timedelta(days=2, hours=9),
                          vehicle_id=v2.id),
            LogisticsPlan(order_id=order4.id, delivery_note_id=dn2.id,
                          plan_type="delivery", planned_datetime=now - datetime.timedelta(days=2),
                          vehicle_id=v1.id),
        ]
        db.session.add_all(plans)
        db.session.flush()

        # ── 13. Invoice (from confirmed+delivered dn2) ─────────────────
        print("Creating invoices...")
        invoice1 = Invoice(
            partner_id=partner_d.id,
            total=375.00,  # 325 + 50
            status="draft",
        )
        db.session.add(invoice1)
        db.session.flush()

        inv_items = [
            InvoiceItem(invoice_id=invoice1.id, source_delivery_id=dn2.id,
                        description="Cement CEM I 42.5 (50 ks x 6.50)",
                        quantity=50, unit_price=6.50, total=325.00),
            InvoiceItem(invoice_id=invoice1.id, source_delivery_id=dn2.id,
                        description="Nakládka materiálu (2 x 25.00)",
                        quantity=2, unit_price=25.00, total=50.00),
        ]
        db.session.add_all(inv_items)

        # ── 14. Audit Log entries ──────────────────────────────────────
        print("Creating audit log entries...")
        audit_entries = [
            AuditLog(user_id=admin.id, action="create", entity_type="order",
                     entity_id=order1.id, details=f"partner={partner_a.id}"),
            AuditLog(user_id=operator.id, action="create", entity_type="order",
                     entity_id=order2.id, details=f"partner={partner_b.id}"),
            AuditLog(user_id=admin.id, action="confirm", entity_type="order",
                     entity_id=order1.id, details="confirmed"),
            AuditLog(user_id=admin.id, action="create", entity_type="delivery_note",
                     entity_id=dn1.id, details="created"),
            AuditLog(user_id=operator.id, action="confirm", entity_type="delivery_note",
                     entity_id=dn2.id, details="confirmed"),
            AuditLog(user_id=admin.id, action="create", entity_type="invoice",
                     entity_id=invoice1.id, details=f"partner={partner_d.id}"),
        ]
        db.session.add_all(audit_entries)

        # ── Commit everything ──────────────────────────────────────────
        db.session.commit()
        print()
        print("=" * 60)
        print("  Mock data seeded successfully!")
        print("=" * 60)
        print()
        print("  Test accounts:")
        print("  ┌──────────────┬───────────────┬──────────┐")
        print("  │ Username     │ Password      │ Role     │")
        print("  ├──────────────┼───────────────┼──────────┤")
        print("  │ admin        │ admin123      │ admin    │")
        print("  │ operator     │ operator123   │ operator │")
        print("  │ zberač       │ collector123  │ collector│")
        print("  │ zakaznik     │ customer123   │ customer │")
        print("  └──────────────┴───────────────┴──────────┘")
        print()
        print("  Data summary:")
        print(f"    Users:           {User.query.count()}")
        print(f"    Partners:        {Partner.query.count()}")
        print(f"    Addresses:       {PartnerAddress.query.count()}")
        print(f"    Contacts:        {Contact.query.count()}")
        print(f"    Products:        {Product.query.count()}")
        print(f"    Bundles:         {Bundle.query.count()}")
        print(f"    Orders:          {Order.query.count()}")
        print(f"    Order Items:     {OrderItem.query.count()}")
        print(f"    Delivery Notes:  {DeliveryNote.query.count()}")
        print(f"    Delivery Items:  {DeliveryItem.query.count()}")
        print(f"    Vehicles:        {Vehicle.query.count()}")
        print(f"    Schedules:       {VehicleSchedule.query.count()}")
        print(f"    Logistics Plans: {LogisticsPlan.query.count()}")
        print(f"    Invoices:        {Invoice.query.count()}")
        print(f"    Invoice Items:   {InvoiceItem.query.count()}")
        print(f"    Audit Logs:      {AuditLog.query.count()}")
        print()
        print("  Run the app with:  python app.py")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the database with mock data")
    parser.add_argument("--append", action="store_true",
                        help="Append data without dropping tables")
    args = parser.parse_args()
    seed(append=args.append)
