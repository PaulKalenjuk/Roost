"""Data model for the Airbnb short-stay ledger.

Conventions
-----------
* Money: ``DecimalField(max_digits=12, decimal_places=2)`` (AUD).
* Percentages/shares: ``DecimalField(max_digits=6, decimal_places=4)`` where a
  stored value of ``0.3500`` means 35%.  ``apportionment_pct`` on an Expense is
  the **deductible** fraction; ``business_use_pct`` on an Asset likewise.
* Dates: local (Australia/Adelaide) calendar dates via ``USE_TZ`` datetimes
  where a timestamp is genuinely needed.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from decimal import InvalidOperation

from .constants import ATO_RENTAL_HUB_URL

ZERO = Decimal("0.00")
ONE = Decimal("1")

# Decline-in-value methods (shared by Property default and Asset).
DEPRECIATION_METHOD_CHOICES = [
    ("diminishing_value", "Diminishing value"),
    ("prime_cost", "Prime cost (straight line)"),
]


def _as_decimal(value):
    """Coerce a Decimal / number / numeric string to ``Decimal`` (or ``None``).

    Model attributes can hold raw strings on an unsaved instance (e.g. when
    created from a form or a script), so computed properties coerce defensively.
    """
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Property / listing
# ---------------------------------------------------------------------------
class Property(TimestampedModel):
    """A physical dwelling; all or part of it may be short-stay let."""

    name = models.CharField(max_length=200)
    address = models.TextField(blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )

    # Floor areas drive the "percentage of floor area" apportionment.
    total_floor_area_sqm = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Whole dwelling / property floor area (m²).",
    )
    rental_floor_area_sqm = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Floor area exclusively used for the short-stay let (m²).",
    )

    # Manual override of the let share.  If set, it wins over the floor-area
    # calculation (e.g. 0.3000 = 30% of the dwelling is let).
    let_percentage = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=(
            "Fraction of the dwelling let for income (0..1). Leave blank to "
            "derive it from the floor areas above. E.g. 0.3000 = 30%."
        ),
    )

    gst_registered = models.BooleanField(
        default=False,
        help_text="Is the host registered for GST? Affects GST treatment on income/expenses.",
    )
    default_depreciation_method = models.CharField(
        max_length=20,
        choices=DEPRECIATION_METHOD_CHOICES,
        default="diminishing_value",
        help_text=(
            "Default decline-in-value method for new assets. See the ATO for "
            "the implications: " + ATO_RENTAL_HUB_URL
        ),
    )

    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "property"
        verbose_name_plural = "properties"
        ordering = ["name"]

    @property
    def rental_area_share(self):
        """Fraction of floor area used for the let, 0..1, or ``None``."""
        total = _as_decimal(self.total_floor_area_sqm)
        rental = _as_decimal(self.rental_floor_area_sqm)
        if total is not None and rental is not None and total > 0:
            return (rental / total).quantize(Decimal("0.000001"))
        return None

    @property
    def let_share(self):
        """Resolved share of the dwelling that is let for income.

        An explicit ``let_percentage`` overrides the floor-area calculation;
        falls back to ``rental_area_share``; ``None`` if neither is available.
        """
        let_pct = _as_decimal(self.let_percentage)
        if let_pct is not None:
            return let_pct
        return self.rental_area_share

    @property
    def ownership_total(self):
        """Sum of ownership shares (fractions). Should be 1 when configured."""
        total = self.ownerships.aggregate(models.Sum("share_pct"))["share_pct__sum"]
        return total or ZERO

    def __str__(self):
        return self.name


class Listing(TimestampedModel):
    """A platform listing (e.g. the Airbnb listing) belonging to a property."""

    PLATFORM_AIRBNB = "airbnb"
    PLATFORM_OTHER = "other"
    PLATFORM_CHOICES = [
        (PLATFORM_AIRBNB, "Airbnb"),
        (PLATFORM_OTHER, "Other platform"),
    ]

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="listings"
    )
    platform = models.CharField(
        max_length=20, choices=PLATFORM_CHOICES, default=PLATFORM_AIRBNB
    )
    name = models.CharField(max_length=200)
    external_id = models.CharField(
        max_length=100, blank=True, help_text="Airbnb listing id (optional)."
    )
    url = models.URLField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["property__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_platform_display()})"


class Owner(TimestampedModel):
    """A person with an ownership interest in one or more properties."""

    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PropertyOwnership(TimestampedModel):
    """An owner's fractional interest in a property (shares should sum to 1)."""

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="ownerships"
    )
    owner = models.ForeignKey(
        Owner, on_delete=models.CASCADE, related_name="ownerships"
    )
    share_pct = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Ownership fraction (0..1). E.g. 0.5000 = 50%.",
    )

    class Meta:
        ordering = ["property__name", "owner__name"]
        verbose_name_plural = "property ownerships"
        constraints = [
            models.UniqueConstraint(
                fields=["property", "owner"], name="uniq_ownership_per_property"
            )
        ]

    def clean(self):
        if self.share_pct is not None and not (0 <= self.share_pct <= 1):
            raise ValidationError({"share_pct": "Must be between 0 and 1."})

    def __str__(self):
        return f"{self.owner} · {self.share_pct * 100:.2f}% of {self.property}"


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
class Category(TimestampedModel):
    """Expense category, with a default apportionment policy."""

    KIND_OPERATING = "operating"
    KIND_UTILITY = "utility"
    KIND_CAPITAL_WORKS = "capital_works"
    KIND_MORTGAGE = "mortgage"
    KIND_OTHER = "other"
    KIND_CHOICES = [
        (KIND_OPERATING, "Operating expense"),
        (KIND_UTILITY, "Utility (shared — apportioned)"),
        (KIND_CAPITAL_WORKS, "Capital works / structural"),
        (KIND_MORTGAGE, "Interest / mortgage"),
        (KIND_OTHER, "Other"),
    ]

    APPORTION_NONE = "none"
    APPORTION_AREA = "area"
    APPORTION_CUSTOM = "custom"
    APPORTION_CHOICES = [
        (APPORTION_NONE, "No apportionment (100% deductible)"),
        (APPORTION_AREA, "By floor area of the let"),
        (APPORTION_CUSTOM, "Custom percentage"),
    ]

    name = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_OPERATING)
    default_apportionment = models.CharField(
        max_length=10, choices=APPORTION_CHOICES, default=APPORTION_NONE
    )
    details = models.CharField(
        max_length=255,
        blank=True,
        help_text="Accountant-facing label / notes.",
    )

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Income
# ---------------------------------------------------------------------------
class Reservation(TimestampedModel):
    """One Airbnb reservation / payout line.

    Populated by the CSV importer (and, later, a browser importer).  Amounts are
    gross-of-platform-fee; ``net_payout`` is what actually hit the bank.
    """

    STATUS_CHOICES = [
        ("reserved", "Reserved"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("pending", "Pending"),
    ]

    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="reservations"
    )
    confirmation_code = models.CharField(
        max_length=64,
        help_text="Airbnb confirmation code — used to de-duplicate imports.",
    )
    guest_name = models.CharField(max_length=200, blank=True)

    check_in = models.DateField(null=True, blank=True)
    check_out = models.DateField(null=True, blank=True)
    nights = models.PositiveIntegerField(null=True, blank=True)

    currency = models.CharField(max_length=3, default="AUD")

    # Money breakdown as reported by Airbnb.
    accommodation_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO
    )
    cleaning_fee = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    gross_earnings = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO
    )
    airbnb_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        help_text="Airbnb service fee (positive number = amount withheld).",
    )
    taxes_collected = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    net_payout = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    payout_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="completed")

    # Provenance
    source = models.CharField(max_length=30, default="airbnb_csv")
    external_id = models.CharField(max_length=120, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-check_in", "-payout_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "confirmation_code"],
                name="uniq_reservation_per_listing",
            )
        ]

    @property
    def gross_before_fee(self):
        return (self.accommodation_amount or ZERO) + (self.cleaning_fee or ZERO)

    def __str__(self):
        when = self.check_in.isoformat() if self.check_in else "?"
        return f"{self.confirmation_code or '—'} · {when} · {self.guest_name or 'guest'}"


class MonthlyEarnings(TimestampedModel):
    """One month of Airbnb earnings for a listing (from the earnings PDF).

    These are *overwritten* on re-import — the Airbnb report is the source of
    truth for the month, so an updated PDF replaces the stored totals.
    """

    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="monthly_earnings"
    )
    #: First day of the month the figures relate to.
    month = models.DateField()

    currency = models.CharField(max_length=3, default="AUD")
    gross_earnings = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    adjustments = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    service_fees = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        help_text="Airbnb service fees (negative = withheld).",
    )
    tax_withheld = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    total_earnings = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO, help_text="Net total (AUD)."
    )

    source = models.CharField(max_length=30, default="airbnb_pdf")
    source_file = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-month"]
        verbose_name_plural = "monthly earnings"
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "month"], name="uniq_monthly_earnings"
            )
        ]

    @property
    def financial_year(self):
        from . import fiscal

        return fiscal.fy_label(self.month)

    def __str__(self):
        return f"{self.month:%b %Y} · {self.total_earnings}"


# ---------------------------------------------------------------------------
# Expenses & receipts
# ---------------------------------------------------------------------------
class Expense(TimestampedModel):
    """A cost incurred against a property (or the let specifically)."""

    APPORTION_NONE = Category.APPORTION_NONE
    APPORTION_AREA = Category.APPORTION_AREA
    APPORTION_CUSTOM = Category.APPORTION_CUSTOM
    APPORTION_CHOICES = Category.APPORTION_CHOICES

    SOURCE_CHOICES = [
        ("manual", "Manual entry"),
        ("airbnb_csv", "Airbnb CSV import"),
        ("utility_bill", "Utility bill"),
        ("other", "Other import"),
    ]

    KIND_ADHOC = "adhoc"
    KIND_UTILITY = "utility"
    KIND_CHOICES = [
        (KIND_ADHOC, "Ad hoc expense"),
        (KIND_UTILITY, "Utility bill"),
    ]

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="expenses"
    )
    listing = models.ForeignKey(
        Listing,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses",
        help_text="Set only if the cost relates to the listing specifically.",
    )
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="expenses"
    )
    kind = models.CharField(
        max_length=20,
        choices=KIND_CHOICES,
        default=KIND_ADHOC,
        help_text="Ad hoc cost, or a utility bill (managed via a utility type).",
    )

    date = models.DateField(default=timezone.localdate)
    vendor = models.CharField(max_length=200, blank=True)
    description = models.CharField(max_length=255, blank=True)

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Gross amount including GST.",
    )
    gst_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        help_text="GST included in ``amount`` (for GST-registered entities).",
    )

    apportionment = models.CharField(
        max_length=10, choices=APPORTION_CHOICES, default=APPORTION_NONE
    )
    apportionment_pct = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Override fraction (0..1). Leave blank to use the floor-area share.",
    )
    # Computed on save() — the amount actually claimable.
    deductible_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO, editable=False
    )

    paid = models.BooleanField(default=True)
    payment_method = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)

    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default="manual")
    external_id = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def compute_apportionment_pct(self):
        """Resolve the deductible fraction for this expense.

        Explicit ``apportionment_pct`` wins; otherwise floor-area share of the
        property is used for ``APPORTION_AREA``.
        """
        from .apportionment import resolve_share

        if self.apportionment == self.APPORTION_CUSTOM and self.apportionment_pct is not None:
            return self.apportionment_pct
        return resolve_share(self)

    def clean(self):
        if self.apportionment == self.APPORTION_CUSTOM and self.apportionment_pct is None:
            raise ValidationError(
                {"apportionment_pct": "Required when apportionment is 'custom'."}
            )
        if self.apportionment_pct is not None and not (0 <= self.apportionment_pct <= 1):
            raise ValidationError({"apportionment_pct": "Must be between 0 and 1."})

    def save(self, *args, **kwargs):
        share = self.compute_apportionment_pct()
        share = _as_decimal(share)
        if share is None:
            share = ONE
        amount = _as_decimal(self.amount)
        if amount is None:
            amount = ZERO
        self.deductible_amount = (amount * share).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.date} · {self.category} · {self.amount}"


class Receipt(TimestampedModel):
    """An uploaded receipt / invoice file, attached to an expense or asset."""

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="receipts"
    )
    file = models.FileField(upload_to="receipts/%Y/%m/")
    original_name = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)

    expense = models.ForeignKey(
        Expense,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="receipts",
    )
    asset = models.ForeignKey(
        "Asset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="receipts",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name or (self.file.name if self.file else "receipt")


# ---------------------------------------------------------------------------
# Depreciating assets
# ---------------------------------------------------------------------------
class Asset(TimestampedModel):
    """A depreciating asset in the rental (plant & equipment, capital works…)."""

    KIND_PLANT = "plant_equipment"
    KIND_CAPITAL_WORKS = "capital_works"
    KIND_OTHER = "other"
    KIND_CHOICES = [
        (KIND_PLANT, "Plant & equipment"),
        (KIND_CAPITAL_WORKS, "Capital works (building)"),
        (KIND_OTHER, "Other"),
    ]

    METHOD_PRIME = "prime_cost"
    METHOD_DIMINISHING = "diminishing_value"
    METHOD_CHOICES = [
        (METHOD_PRIME, "Prime cost (straight line)"),
        (METHOD_DIMINISHING, "Diminishing value"),
    ]

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="assets"
    )
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_PLANT)

    purchase_date = models.DateField()
    cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Cost (incl. any installation / GST as applicable).",
    )
    # Effective life in years (ATO schedule). Decimals allowed (e.g. 6.5).
    effective_life_years = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    method = models.CharField(
        max_length=20, choices=DEPRECIATION_METHOD_CHOICES, default="diminishing_value"
    )
    business_use_pct = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=ONE,
        help_text="Business/rental use fraction (0..1).",
    )
    low_value_pool = models.BooleanField(
        default=False,
        help_text="Pooled (e.g. cost below the ATO low-value threshold).",
    )

    disposed_date = models.DateField(null=True, blank=True)
    disposal_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-purchase_date", "name"]

    def is_disposed(self):
        """NB: a plain method, not @property — the ``property`` FK in this class
        shadows the ``property`` builtin."""
        return self.disposed_date is not None

    is_disposed.boolean = True
    is_disposed.short_description = "disposed"

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"


class DepreciationEntry(TimestampedModel):
    """One financial year's depreciation for an asset (computed, not hand-edited)."""

    asset = models.ForeignKey(
        Asset, on_delete=models.CASCADE, related_name="depreciation_entries"
    )
    financial_year = models.CharField(max_length=12, help_text="e.g. FY2025-26")
    method = models.CharField(max_length=20, choices=Asset.METHOD_CHOICES)
    opening_value = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    deduction = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    closing_value = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    days_held = models.PositiveIntegerField(null=True, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset", "financial_year"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "financial_year"],
                name="uniq_depreciation_per_asset_fy",
            )
        ]
        verbose_name_plural = "depreciation entries"

    def __str__(self):
        return f"{self.asset.name} · {self.financial_year} · {self.deduction}"


# ---------------------------------------------------------------------------
# Import bookkeeping
# ---------------------------------------------------------------------------
class ImportBatch(TimestampedModel):
    """Audit trail for an importer run."""

    SOURCE_CHOICES = [
        ("airbnb_csv", "Airbnb transaction CSV"),
        ("airbnb_pdf", "Airbnb earnings-report PDF"),
        ("airbnb_browser", "Airbnb browser pull"),
        ("other", "Other"),
    ]

    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default="airbnb_csv")
    listing = models.ForeignKey(
        Listing, null=True, blank=True, on_delete=models.SET_NULL, related_name="import_batches"
    )
    filename = models.CharField(max_length=255, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    rows_total = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    log = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "import batches"

    def __str__(self):
        return f"{self.source} · {self.filename or '—'} · {self.created_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
class UtilityType(TimestampedModel):
    """A recurring utility for a property (electricity, water, internet…)."""

    FREQ_MONTHLY = "monthly"
    FREQ_QUARTERLY = "quarterly"
    FREQ_HALF_YEARLY = "half_yearly"
    FREQ_YEARLY = "yearly"
    FREQ_OTHER = "other"
    FREQUENCY_CHOICES = [
        (FREQ_MONTHLY, "Monthly"),
        (FREQ_QUARTERLY, "Quarterly"),
        (FREQ_HALF_YEARLY, "Half-yearly"),
        (FREQ_YEARLY, "Yearly"),
        (FREQ_OTHER, "Other"),
    ]

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="utility_types"
    )
    name = models.CharField(max_length=120, help_text="e.g. Electricity")
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="utility_types"
    )
    frequency = models.CharField(
        max_length=20, choices=FREQUENCY_CHOICES, default=FREQ_QUARTERLY
    )
    supplier = models.CharField(max_length=200, blank=True)
    coverage_start = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Start tracking coverage from this date. Leave blank to use the "
            "property's purchase date."
        ),
    )
    apportionment = models.CharField(
        max_length=10,
        choices=Category.APPORTION_CHOICES,
        default=Category.APPORTION_AREA,
        help_text="How the claimable portion is worked out (usually by floor area).",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["property__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "name"], name="uniq_utility_type_per_property"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"


class UtilityBill(TimestampedModel):
    """One bill for a utility type. Saving it keeps a linked Expense in sync."""

    utility_type = models.ForeignKey(
        UtilityType, on_delete=models.CASCADE, related_name="bills"
    )
    bill_date = models.DateField(null=True, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO,
        help_text="Total bill amount (before apportionment).",
    )
    gst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    paid = models.BooleanField(default=True)

    attachment = models.FileField(
        upload_to="bills/%Y/%m/", blank=True, null=True,
        help_text="The bill PDF/image.",
    )
    #: Raw AI extraction (amount, dates, supplier…) kept for audit.
    extracted = models.JSONField(default=dict, blank=True)

    notes = models.TextField(blank=True)
    expense = models.OneToOneField(
        Expense, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="utility_bill",
    )

    class Meta:
        ordering = ["-bill_date", "-id"]

    @property
    def claimable_amount(self):
        return self.expense.deductible_amount if self.expense_id else None

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        expense = self.expense or Expense()
        expense.property = self.utility_type.property
        expense.category = self.utility_type.category
        expense.kind = Expense.KIND_UTILITY
        expense.date = self.bill_date or self.period_end or timezone.localdate()
        expense.description = f"{self.utility_type.name} bill"
        expense.vendor = self.utility_type.supplier or expense.vendor
        expense.amount = self.amount
        expense.gst_amount = self.gst_amount
        expense.apportionment = self.utility_type.apportionment
        expense.source = "utility_bill"
        expense.save()
        if self.expense_id != expense.id:
            self.expense = expense
            super().save(update_fields=["expense"])

    def __str__(self):
        when = self.bill_date or self.period_end
        return f"{self.utility_type.name} · {when} · {self.amount}"
