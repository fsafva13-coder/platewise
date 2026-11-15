from datetime import timedelta

from odoo import api, models, fields

# Ne'ma-style "meal equivalent": average plated meal weight used to express waste as meals.
KG_PER_MEAL = 0.5


class PlatewiseOutlet(models.Model):
    _name = "platewise.outlet"
    _description = "Kitchen Outlet"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(string="Outlet", required=True, tracking=True)
    color = fields.Integer()
    outlet_type = fields.Selection(
        selection=[
            ("buffet", "Buffet Restaurant"),
            ("a_la_carte", "A la Carte"),
            ("banquet", "Banquets & Events"),
            ("room_service", "In-Room Dining"),
            ("staff", "Staff Canteen"),
        ],
        default="buffet", required=True,
    )
    manager_id = fields.Many2one("res.users", string="Outlet Manager", default=lambda self: self.env.user, tracking=True)
    daily_budget_aed = fields.Float(
        string="Daily Waste Budget (AED)", default=500.0, tracking=True,
        help="Maximum acceptable cost of food waste per day. The manager is alerted when it is exceeded.",
    )
    waste_log_ids = fields.One2many("platewise.waste.log", "outlet_id", string="Waste Logs")
    prep_plan_ids = fields.One2many("platewise.prep.plan", "outlet_id", string="Prep Plans")

    # ---- This month KPIs (dashboard) ----
    month_waste_kg = fields.Float(string="Wasted this month (kg)", compute="_compute_month_kpis")
    month_cost_aed = fields.Float(string="Lost this month (AED)", compute="_compute_month_kpis")
    month_co2_kg = fields.Float(string="CO2e this month (kg)", compute="_compute_month_kpis")
    month_meals = fields.Integer(string="Meals equivalent", compute="_compute_month_kpis")
    month_avoidable_pct = fields.Float(string="Avoidable share (%)", compute="_compute_month_kpis")
    today_cost_aed = fields.Float(string="Lost today (AED)", compute="_compute_month_kpis")
    budget_status = fields.Selection(
        selection=[("ok", "Within budget"), ("warning", "Near budget"), ("over", "Over budget")],
        compute="_compute_month_kpis",
    )
    waste_log_count = fields.Integer(compute="_compute_month_kpis")

    _check_daily_budget = models.Constraint("CHECK(daily_budget_aed >= 0)", "The daily waste budget cannot be negative!")

    def _month_logs(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        return self.waste_log_ids.filtered(
            lambda log: log.state == "confirmed" and log.date and log.date >= today.replace(day=1) and log.date <= today
        )

    @api.depends("waste_log_ids.cost_aed", "waste_log_ids.state", "waste_log_ids.date", "daily_budget_aed")
    def _compute_month_kpis(self):
        today = fields.Date.context_today(self)
        for outlet in self:
            logs = outlet._month_logs()
            kg = sum(logs.mapped("quantity_kg"))
            cost = sum(logs.mapped("cost_aed"))
            avoidable = sum(logs.filtered("avoidable").mapped("cost_aed"))
            today_cost = sum(logs.filtered(lambda log: log.date == today).mapped("cost_aed"))
            # Budget status uses average daily cost this month vs. the daily budget
            days = max(today.day, 1)
            avg_daily = cost / days
            outlet.month_waste_kg = kg
            outlet.month_cost_aed = cost
            outlet.month_co2_kg = sum(logs.mapped("co2_kg"))
            outlet.month_meals = int(kg / KG_PER_MEAL)
            outlet.month_avoidable_pct = (avoidable / cost * 100.0) if cost else 0.0
            outlet.today_cost_aed = today_cost
            outlet.waste_log_count = len(outlet.waste_log_ids)
            if not outlet.daily_budget_aed or avg_daily <= outlet.daily_budget_aed * 0.8:
                outlet.budget_status = "ok"
            elif avg_daily <= outlet.daily_budget_aed:
                outlet.budget_status = "warning"
            else:
                outlet.budget_status = "over"

    def action_view_waste_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Waste Logs - %s" % self.name,
            "res_model": "platewise.waste.log",
            "view_mode": "list,form,pivot,graph",
            "domain": [("outlet_id", "=", self.id)],
            "context": {"default_outlet_id": self.id},
        }

    def action_log_waste(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Log Waste",
            "res_model": "platewise.waste.log",
            "view_mode": "form",
            "view_id": self.env.ref("platewise.platewise_waste_log_view_form_quick").id,
            "target": "new",
            "context": {"default_outlet_id": self.id},
        }

    # ---- Report helper ----
    def _get_report_data(self):
        """Breakdown used by the monthly Food Waste Impact report."""
        self.ensure_one()
        logs = self._month_logs()
        reasons = dict(self.env["platewise.waste.log"]._fields["reason"].selection)
        by_reason = []
        for key, label in reasons.items():
            r_logs = logs.filtered(lambda log: log.reason == key)
            if r_logs:
                by_reason.append({
                    "label": label,
                    "kg": sum(r_logs.mapped("quantity_kg")),
                    "cost": sum(r_logs.mapped("cost_aed")),
                })
        by_reason.sort(key=lambda r: r["cost"], reverse=True)
        by_dish = {}
        for log in logs:
            row = by_dish.setdefault(log.dish_id.name, {"label": log.dish_id.name, "kg": 0.0, "cost": 0.0})
            row["kg"] += log.quantity_kg
            row["cost"] += log.cost_aed
        top_dishes = sorted(by_dish.values(), key=lambda r: r["cost"], reverse=True)[:5]
        today = fields.Date.context_today(self)
        return {
            "period": today.strftime("%B %Y"),
            "by_reason": by_reason,
            "top_dishes": top_dishes,
            # If avoidable waste is halved (UAE Ne'ma 2030 target), projected monthly saving:
            "saving_if_halved": self.month_cost_aed * (self.month_avoidable_pct / 100.0) / 2.0,
        }

    # ---- Scheduled action ----
    @api.model
    def _cron_check_waste_budget(self):
        """Daily: alert the outlet manager when yesterday's waste cost exceeded the budget."""
        yesterday = fields.Date.context_today(self) - timedelta(days=1)
        for outlet in self.search([("daily_budget_aed", ">", 0)]):
            logs = outlet.waste_log_ids.filtered(lambda log: log.state == "confirmed" and log.date == yesterday)
            cost = sum(logs.mapped("cost_aed"))
            if cost <= outlet.daily_budget_aed:
                continue
            # Avoid duplicate alerts if the job runs more than once a day
            if outlet.activity_ids.filtered(lambda a: a.summary == "Review food waste: budget exceeded"):
                continue
            top = logs.sorted("cost_aed", reverse=True)[:1]
            note = "Food waste cost AED %.2f on %s (budget AED %.2f)." % (cost, yesterday, outlet.daily_budget_aed)
            if top:
                note += " Biggest item: %s (%.1f kg, %s)." % (
                    top.dish_id.name, top.quantity_kg, dict(top._fields["reason"].selection).get(top.reason))
            outlet.message_post(body=note, subject="Waste budget exceeded")
            if outlet.manager_id:
                outlet.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=outlet.manager_id.id,
                    summary="Review food waste: budget exceeded",
                    note=note,
                )
