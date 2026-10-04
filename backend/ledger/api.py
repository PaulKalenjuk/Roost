"""Roost REST API.

Session-authenticated (the same login as the Django admin) and CSRF-protected,
which suits a same-origin single-page app served by this Django project.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth import authenticate, login, logout
from django.core.files.base import ContentFile
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from . import coverage as coverage_lib
from . import depreciation as depreciation_lib
from . import fiscal, pdf, receipts_zip, reports
from .importers import airbnb_pdf
from .models import (
    Asset,
    Category,
    EarningsSummary,
    Expense,
    ImportBatch,
    Listing,
    MonthlyEarnings,
    Owner,
    Property,
    PropertyOwnership,
    Receipt,
    Reservation,
    UnallocatedExpense,
    UtilityBill,
    UtilityType,
)
from .serializers import (
    AssetSerializer,
    CategorySerializer,
    EarningsSummarySerializer,
    ExpenseSerializer,
    ImportBatchSerializer,
    ListingSerializer,
    MonthlyEarningsSerializer,
    OwnerSerializer,
    PropertyOwnershipSerializer,
    PropertySerializer,
    ReceiptSerializer,
    ReservationSerializer,
    UnallocatedExpenseSerializer,
    UtilityBillSerializer,
    UtilityTypeSerializer,
)
from .services import asset_extract, bill_extract, capture_extract, expense_extract


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([AllowAny])
def csrf(request):
    """Return (and set) a CSRF token for the SPA to echo back."""
    return Response({"csrfToken": get_token(request)})


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    user = authenticate(
        request,
        username=request.data.get("username", ""),
        password=request.data.get("password", ""),
    )
    if user is None:
        return Response({"detail": "Invalid username or password."},
                        status=status.HTTP_401_UNAUTHORIZED)
    login(request, user)
    return Response({"username": user.username, "csrfToken": get_token(request)})


@api_view(["POST"])
def logout_view(request):
    logout(request)
    return Response({"ok": True})


@api_view(["GET"])
def me(request):
    return Response({
        "authenticated": request.user.is_authenticated,
        "username": request.user.get_username(),
        "is_staff": request.user.is_staff,
    })


# ---------------------------------------------------------------------------
# CRUD viewsets
# ---------------------------------------------------------------------------
class PropertyViewSet(viewsets.ModelViewSet):
    queryset = Property.objects.all()
    serializer_class = PropertySerializer

    @action(detail=True, methods=["post"], url_path="clear-image")
    def clear_image(self, request, pk=None):
        """Drop the property image (and its file) — empties the multipart dance."""
        prop = self.get_object()
        if prop.image:
            prop.image.delete(save=True)
        return Response(self.get_serializer(prop).data)


class OwnerViewSet(viewsets.ModelViewSet):
    queryset = Owner.objects.all()
    serializer_class = OwnerSerializer


class ListingViewSet(viewsets.ModelViewSet):
    queryset = Listing.objects.select_related("property")
    serializer_class = ListingSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        prop = self.request.query_params.get("property")
        return qs.filter(property_id=prop) if prop else qs


class PropertyOwnershipViewSet(viewsets.ModelViewSet):
    serializer_class = PropertyOwnershipSerializer

    def get_queryset(self):
        qs = PropertyOwnership.objects.select_related("owner")
        prop = self.request.query_params.get("property")
        return qs.filter(property_id=prop) if prop else qs


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    def destroy(self, request, *args, **kwargs):
        """Categories are PROTECTed by expenses/utility types — say so nicely."""
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {
                    "detail": "This category is in use by expenses or utility types, "
                              "so it can't be deleted. Rename it instead, or move those "
                              "records to another category first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


class ExpenseViewSet(viewsets.ModelViewSet):
    serializer_class = ExpenseSerializer

    def get_queryset(self):
        qs = Expense.objects.select_related("category", "property")
        kind = self.request.query_params.get("kind")
        prop = self.request.query_params.get("property")
        if kind:
            qs = qs.filter(kind=kind)
        if prop:
            qs = qs.filter(property_id=prop)
        return qs


class UtilityTypeViewSet(viewsets.ModelViewSet):
    serializer_class = UtilityTypeSerializer

    def get_queryset(self):
        qs = UtilityType.objects.select_related("category", "property")
        prop = self.request.query_params.get("property")
        return qs.filter(property_id=prop) if prop else qs

    @action(detail=True, methods=["get"], url_path="coverage")
    def coverage(self, request, pk=None):
        return Response(coverage_lib.coverage_for(self.get_object()))


class UtilityBillViewSet(viewsets.ModelViewSet):
    serializer_class = UtilityBillSerializer

    def get_queryset(self):
        qs = UtilityBill.objects.select_related("utility_type", "expense")
        ut = self.request.query_params.get("utility_type")
        return qs.filter(utility_type_id=ut) if ut else qs


class AssetViewSet(viewsets.ModelViewSet):
    serializer_class = AssetSerializer

    def get_queryset(self):
        qs = Asset.objects.select_related("property")
        prop = self.request.query_params.get("property")
        return qs.filter(property_id=prop) if prop else qs

    @action(detail=False, methods=["post"], url_path="recompute")
    def recompute(self, request):
        prop = request.data.get("property")
        assets = Asset.objects.all()
        if prop:
            assets = assets.filter(property_id=prop)
        count = 0
        for asset in assets:
            depreciation_lib.recompute_schedule(asset)
            count += 1
        return Response({"recomputed": count})

    @action(detail=True, methods=["post"], url_path="recompute")
    def recompute_one(self, request, pk=None):
        asset = self.get_object()
        entries = depreciation_lib.recompute_schedule(asset)
        return Response({"entries": len(entries)})


class ReservationViewSet(viewsets.ModelViewSet):
    serializer_class = ReservationSerializer

    def get_queryset(self):
        qs = Reservation.objects.select_related("listing")
        listing = self.request.query_params.get("listing")
        return qs.filter(listing_id=listing) if listing else qs


class MonthlyEarningsViewSet(viewsets.ModelViewSet):
    serializer_class = MonthlyEarningsSerializer

    def get_queryset(self):
        qs = MonthlyEarnings.objects.select_related("listing")
        listing = self.request.query_params.get("listing")
        return qs.filter(listing_id=listing) if listing else qs


class EarningsSummaryViewSet(viewsets.ModelViewSet):
    """Period totals (nights booked, averages). Editable so nights can be fixed."""

    serializer_class = EarningsSummarySerializer

    def get_queryset(self):
        qs = EarningsSummary.objects.select_related("listing")
        listing = self.request.query_params.get("listing")
        fy = self.request.query_params.get("fy")
        if listing:
            qs = qs.filter(listing_id=listing)
        if fy:
            qs = qs.filter(financial_year=fy)
        return qs


class ReceiptViewSet(viewsets.ModelViewSet):
    serializer_class = ReceiptSerializer
    queryset = Receipt.objects.all()


class UnallocatedExpenseViewSet(viewsets.ModelViewSet):
    """The unallocated-expense area: photographed receipts awaiting a category.

    * ``POST /api/unallocated/`` — upload a photo + highlight boxes.
    * ``POST /api/unallocated/<id>/process/`` — read the highlighted lines with
      the vision model, sum them and suggest a category.
    * ``POST /api/unallocated/<id>/allocate/`` — create the real ledger expense
      (with the photo attached as its receipt) and mark this one allocated.
    """

    serializer_class = UnallocatedExpenseSerializer

    def get_queryset(self):
        qs = UnallocatedExpense.objects.select_related("category", "property", "expense")
        prop = self.request.query_params.get("property")
        status_ = self.request.query_params.get("status")
        if prop:
            qs = qs.filter(property_id=prop)
        if status_:
            qs = qs.filter(status=status_)
        return qs

    def _examples_for(self, prop):
        """Recent expenses for the property, to steer the category guess."""
        rows = (
            Expense.objects.filter(property=prop)
            .select_related("category")
            .order_by("-date")[:80]
        )
        return [
            {
                "description": row.description,
                "vendor": row.vendor,
                "category": row.category.name if row.category_id else None,
            }
            for row in rows
        ]

    @action(detail=True, methods=["post"], url_path="process")
    def process(self, request, pk=None):
        capture = self.get_object()
        categories = list(Category.objects.all())
        try:
            data = capture_extract.process_highlights(
                capture.image,
                capture.highlights,
                category_names=[c.name for c in categories],
                examples=self._examples_for(capture.property),
            )
        except bill_extract.BillExtractionUnavailable as exc:
            return Response(
                {"detail": str(exc), "available": False},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        match = _match_category(data.get("category_hint"), categories)
        if data.get("amount") is not None:
            capture.amount = Decimal(str(data["amount"]))
        if data.get("gst_amount") is not None:
            capture.gst_amount = Decimal(str(data["gst_amount"]))
        if data.get("vendor"):
            capture.vendor = data["vendor"]
        if data.get("date"):
            capture.date = data["date"]
        if match is not None:
            capture.category = match
        if data.get("confidence") is not None:
            try:
                capture.confidence = Decimal(str(data["confidence"]))
            except Exception:
                capture.confidence = None
        capture.rationale = data.get("rationale") or ""
        capture.extracted = data
        capture.status = UnallocatedExpense.STATUS_PROCESSED
        capture.save()

        payload = dict(self.get_serializer(capture).data)
        payload["available"] = True
        return Response(payload)

    @action(detail=True, methods=["post"], url_path="allocate")
    def allocate(self, request, pk=None):
        capture = self.get_object()
        if capture.expense_id:
            return Response(
                {"detail": "This receipt has already been allocated."}, status=400
            )

        def pick(name, fallback):
            value = request.data.get(name)
            return value if value not in (None, "") else fallback

        category_id = pick("category", capture.category_id)
        if not category_id:
            return Response({"detail": "category is required."}, status=400)
        category = get_object_or_404(Category, pk=category_id)

        amount = pick("amount", capture.amount)
        if amount in (None, ""):
            return Response(
                {"detail": "amount is required — process with AI, or type it in."},
                status=400,
            )

        expense = Expense(
            property=capture.property,
            category=category,
            kind=Expense.KIND_ADHOC,
            date=pick("date", capture.date),
            vendor=pick("vendor", capture.vendor),
            description=pick("description", capture.description),
            amount=amount,
            gst_amount=pick("gst_amount", capture.gst_amount) or "0",
            apportionment=pick("apportionment", "none"),
            apportionment_pct=request.data.get("apportionment_pct") or None,
            paid=str(pick("paid", "true")).lower() not in ("false", "0", "no"),
            source="receipt_capture",
            notes=capture.note,
        )
        expense.save()

        # Copy the photo bytes into a new receipt file — never share the stored
        # file between the capture and the receipt (deleting one would delete
        # the other's file).
        name = capture.image.name.rsplit("/", 1)[-1] or f"receipt-{capture.id}.jpg"
        receipt = Receipt(
            property=capture.property, expense=expense, original_name=name
        )
        capture.image.open("rb")
        try:
            content = capture.image.read()
        finally:
            capture.image.close()
        receipt.file.save(name, ContentFile(content), save=True)

        capture.expense = expense
        capture.status = UnallocatedExpense.STATUS_ALLOCATED
        if request.data.get("category"):
            capture.category = category
        if request.data.get("amount") not in (None, ""):
            capture.amount = Decimal(str(request.data["amount"]))
        capture.save()

        return Response(
            {
                "expense": ExpenseSerializer(
                    expense, context={"request": request}
                ).data,
                "unallocated": self.get_serializer(capture).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ImportBatchViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ImportBatchSerializer
    queryset = ImportBatch.objects.select_related("listing")

    def get_queryset(self):
        qs = super().get_queryset()
        listing = self.request.query_params.get("listing")
        source = self.request.query_params.get("source")
        if listing:
            qs = qs.filter(listing_id=listing)
        if source:
            qs = qs.filter(source=source)
        return qs


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
@api_view(["POST"])
def import_income_pdf(request):
    """Import an Airbnb earnings-report PDF for a listing (uploads → monthly totals)."""
    upload = request.FILES.get("file")
    listing_id = request.data.get("listing")
    if not upload:
        return Response({"detail": "No file uploaded."}, status=400)
    if not listing_id:
        return Response({"detail": "listing is required."}, status=400)
    listing = get_object_or_404(Listing, pk=listing_id)
    batch = airbnb_pdf.import_report(upload, listing, filename=upload.name)
    return Response(
        ImportBatchSerializer(batch, context={"request": request}).data,
        status=201,
    )


def _match_category(hint, categories):
    """Match an AI category hint against the user's own categories (or None)."""
    hint = (hint or "").strip().lower()
    if not hint:
        return None
    exact = next((c for c in categories if c.name.lower() == hint), None)
    if exact is not None:
        return exact
    return next(
        (c for c in categories if hint in c.name.lower() or c.name.lower() in hint),
        None,
    )


@api_view(["POST"])
def extract_expense(request):
    """Read an uploaded receipt for an ad-hoc expense and return form fields."""
    upload = request.FILES.get("file")
    if not upload:
        return Response({"detail": "No file uploaded."}, status=400)

    categories = list(Category.objects.all())
    try:
        data = expense_extract.extract_expense(
            upload, filename=upload.name, category_names=[c.name for c in categories]
        )
    except bill_extract.BillExtractionUnavailable as exc:
        return Response(
            {"detail": str(exc), "available": False},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # Match the model's category hint against the user's own categories.
    hint = (data.get("category_hint") or "").strip().lower()
    match = None
    if hint:
        match = next((c for c in categories if c.name.lower() == hint), None)
        if match is None:
            match = next(
                (c for c in categories if hint in c.name.lower() or c.name.lower() in hint),
                None,
            )
    if match is not None:
        data["category"] = match.id
        data["category_name"] = match.name
    return Response(data)


@api_view(["POST"])
def extract_asset(request):
    """Read an uploaded purchase receipt and return asset-register fields."""
    upload = request.FILES.get("file")
    if not upload:
        return Response({"detail": "No file uploaded."}, status=400)
    try:
        data = asset_extract.extract_asset(upload, filename=upload.name)
    except bill_extract.BillExtractionUnavailable as exc:
        return Response(
            {"detail": str(exc), "available": False},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response(data)


@api_view(["GET"])
def utilities_coverage(request):
    """Coverage summary for every utility type on a property."""
    prop_id = request.query_params.get("property")
    if not prop_id:
        return Response({"detail": "property is required."}, status=400)
    prop = get_object_or_404(Property, pk=prop_id)
    today = dt.date.today()
    return Response({
        "today": today.isoformat(),
        "coverage": [
            coverage_lib.coverage_for(ut, today)
            for ut in prop.utility_types.select_related("property").order_by("name")
        ],
    })


@api_view(["POST"])
def extract_bill(request):
    """Read an uploaded bill PDF with DeepSeek and return the parsed fields."""
    upload = request.FILES.get("file")
    if not upload:
        return Response({"detail": "No file uploaded."}, status=400)
    try:
        data = bill_extract.extract_bill(upload, filename=upload.name)
    except bill_extract.BillExtractionUnavailable as exc:
        return Response(
            {"detail": str(exc), "available": False},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response(data)


@api_view(["GET"])
def fy_report(request):
    """Per-financial-year report as JSON (optionally split by owner)."""
    prop = get_object_or_404(Property, pk=request.query_params.get("property"))
    label = request.query_params.get("fy") or fiscal.fy_label(dt.date.today())
    recompute = request.query_params.get("recompute") in ("1", "true", "yes")
    include_owners = request.query_params.get("owners") in ("1", "true", "yes")

    report, owners = reports.owner_reports(prop, label, recompute)
    payload = {"report": report, "fy_options": _fy_options()}
    if include_owners:
        payload["owners"] = owners
    return Response(payload)


@api_view(["GET"])
def fy_receipts_zip(request):
    """ZIP of the receipt files behind the year's calculations, by category."""
    prop = get_object_or_404(Property, pk=request.query_params.get("property"))
    label = request.query_params.get("fy") or fiscal.fy_label(dt.date.today())
    include_owners = request.query_params.get("owners") in ("1", "true", "yes")
    show_working = request.query_params.get("working") in ("1", "true", "yes")

    report, owners = reports.owner_reports(prop, label)
    payload = receipts_zip.build_receipts_zip(
        prop,
        label,
        report=report,
        owners=owners if include_owners else [],
        show_working=show_working,
    )
    if payload is None:
        return Response(
            {"detail": f"No receipt files found for {label}."}, status=status.HTTP_404_NOT_FOUND
        )

    safe_name = "".join(c if c.isalnum() or c in "-_." else "-" for c in prop.name)
    response = HttpResponse(payload, content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="roost-{safe_name}-{label}-receipts.zip"'
    )
    return response


@api_view(["GET"])
def fy_report_pdf(request):
    """The same report as a downloadable PDF (honours owners + working flags)."""
    prop = get_object_or_404(Property, pk=request.query_params.get("property"))
    label = request.query_params.get("fy") or fiscal.fy_label(dt.date.today())
    recompute = request.query_params.get("recompute") in ("1", "true", "yes")
    include_owners = request.query_params.get("owners") in ("1", "true", "yes")
    show_working = request.query_params.get("working") in ("1", "true", "yes")

    report, owners = reports.owner_reports(prop, label, recompute)
    try:
        payload = pdf.render_report_pdf(
            report,
            owners if include_owners else [],
            show_working=show_working,
            image=prop.image,
        )
    except pdf.PdfUnavailable as exc:
        return Response(
            {"detail": str(exc), "available": False},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    safe_name = "".join(c if c.isalnum() or c in "-_." else "-" for c in prop.name)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="roost-{safe_name}-{label}.pdf"'
    )
    return response


def _fy_options():
    """A few FY labels around today, newest first, for the UI dropdown."""
    today = dt.date.today()
    this_year = today.year if today.month >= 7 else today.year - 1
    return [f"FY{y}-{str(y + 1)[2:]}" for y in range(this_year, this_year - 6, -1)]
