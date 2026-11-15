from odoo import api, models, fields
from odoo.exceptions import UserError

AVOIDABLE_REASONS = ("overproduction", "spoilage", "expired", "plate_waste")


class PlatewiseWasteLog(models.Model):
    _name = "platewise.waste.log"
    _description = "Food Waste Log"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(string="Reference", required=True, readonly=True, copy=False, default="New")
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    weekday = fields.Selection(
        selection=[("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"), ("3", "Thursday"),
                   ("4", "Friday"), ("5", "Saturday"), ("6", "Sunday")],
        compute="_compute_weekday", store=True,
    )
    shift = fields.Selection(
        selection=[("breakfast", "Breakfast"), ("lunch", "Lunch"), ("dinner", "Dinner"), ("night", "Overnight")],
        default="lunch", required=True,
    )
    outlet_id = fields.Many2one("platewise.outlet", string="Outlet", required=True, tracking=True)
    dish_id = fields.Many2one("platewise.dish", string="Dish", required=True, tracking=True)
    category_id = fields.Many2one(related="dish_id.category_id", store=True, string="Food Category")
    stage = fields.Selection(
        selection=[("storage", "Storage"), ("prep", "Preparation"), ("service", "Buffet / Service"), ("plate", "Guest Plate")],
        string="Where it was wasted", default="service", required=True,
    )
    reason = fields.Selection(
        selection=[
            ("overproduction", "Overproduction"),
            ("spoilage", "Spoilage"),
            ("expired", "Expired"),
            ("plate_waste", "Plate Waste"),
            ("trim", "Trim / Peel"),
            ("damaged", "Damaged / Dropped"),
        ],
        default="overproduction", required=True, tracking=True,
    )
    quantity_kg = fields.Float(string="Quantity (kg)", digits=(16, 2), required=True, tracking=True)
    cost_aed = fields.Float(string="Cost (AED)", digits=(16, 2), compute="_compute_impact", store=True)
    co2_kg = fields.Float(string="CO2e (kg)", digits=(16, 2), compute="_compute_impact", store=True)
    avoidable = fields.Boolean(compute="_compute_impact", store=True,
                               help="Overproduction, spoilage, expiry and plate waste are considered avoidable.")
    logged_by_id = fields.Many2one("res.users", string="Logged by", default=lambda self: self.env.user)
    photo = fields.Image(max_width=1024, max_height=1024)
    note = fields.Text()
    state = fields.Selection(
        selection=[("draft", "Draft"), ("confirmed", "Confirmed"), ("donated", "Redirected to Donation")],
        default="draft", tracking=True,
    )

    _check_quantity = models.Constraint("CHECK(quantity_kg > 0)", "Wasted quantity must be greater than 0!")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("platewise.waste.log") or "New"
        return super().create(vals_list)

    @api.depends("date")
    def _compute_weekday(self):
        for log in self:
            log.weekday = str(log.date.weekday()) if log.date else False

    @api.depends("quantity_kg", "reason", "dish_id.cost_per_kg", "dish_id.category_id.co2_factor")
    def _compute_impact(self):
        for log in self:
            log.cost_aed = log.quantity_kg * log.dish_id.cost_per_kg
            log.co2_kg = log.quantity_kg * log.dish_id.category_id.co2_factor
            log.avoidable = log.reason in AVOIDABLE_REASONS

    def action_confirm(self):
        for log in self:
            if log.state != "draft":
                raise UserError("Only draft waste logs can be confirmed.")
            log.state = "confirmed"

    def action_donate(self):
        for log in self:
            if log.reason not in ("overproduction",) or log.stage in ("plate",):
                raise UserError("Only untouched overproduction can be redirected to food donation.")
            log.state = "donated"

    def action_reset_draft(self):
        for log in self:
            log.state = "draft"

    def action_quick_save(self):
        """Used by the kiosk popup: confirm immediately and open a fresh entry."""
        self.action_confirm()
        return {
            "type": "ir.actions.act_window",
            "name": "Log Waste",
            "res_model": "platewise.waste.log",
            "view_mode": "form",
            "view_id": self.env.ref("platewise.platewise_waste_log_view_form_quick").id,
            "target": "new",
            "context": {
                "default_outlet_id": self.outlet_id.id,
                "default_shift": self.shift,
            },
        }
