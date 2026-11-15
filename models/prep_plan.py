import random
from datetime import timedelta

from odoo import api, models, fields
from odoo.exceptions import UserError

SAFETY_BUFFER = 1.05   # prepare 5% above expected demand
HISTORY_WEEKS = 4      # look back at the last 4 same weekdays


class PlatewisePrepPlan(models.Model):
    _name = "platewise.prep.plan"
    _description = "Daily Prep Plan"
    _order = "date desc, outlet_id, dish_id"

    name = fields.Char(compute="_compute_name", store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    weekday = fields.Selection(
        selection=[("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"), ("3", "Thursday"),
                   ("4", "Friday"), ("5", "Saturday"), ("6", "Sunday")],
        compute="_compute_weekday", store=True,
    )
    outlet_id = fields.Many2one("platewise.outlet", string="Outlet", required=True)
    dish_id = fields.Many2one("platewise.dish", string="Dish", required=True)
    cost_per_kg = fields.Float(related="dish_id.cost_per_kg")
    planned_kg = fields.Float(string="Planned (kg)", digits=(16, 1))
    produced_kg = fields.Float(string="Actually Produced (kg)", digits=(16, 1))
    state = fields.Selection(
        selection=[("planned", "Planned"), ("done", "Done")],
        default="planned", required=True,
    )

    # Results (from waste logs of the same day / outlet / dish)
    wasted_kg = fields.Float(string="Wasted (kg)", digits=(16, 1), compute="_compute_results")
    consumed_kg = fields.Float(string="Consumed (kg)", digits=(16, 1), compute="_compute_results")
    waste_pct = fields.Float(string="Waste %", digits=(16, 1), compute="_compute_results")

    # Forecast
    suggested_kg = fields.Float(string="PlateWise Suggestion (kg)", digits=(16, 1), compute="_compute_suggestion")
    history_count = fields.Integer(string="Days of history used", compute="_compute_suggestion")
    potential_saving_aed = fields.Float(string="Potential Saving (AED)", digits=(16, 2), compute="_compute_suggestion")

    _unique_plan = models.Constraint("UNIQUE(date, outlet_id, dish_id)", "There is already a prep plan for this dish, outlet and date.")
    _check_planned = models.Constraint("CHECK(planned_kg >= 0 AND produced_kg >= 0)", "Quantities cannot be negative!")

    @api.depends("dish_id", "outlet_id", "date")
    def _compute_name(self):
        for plan in self:
            plan.name = "%s - %s (%s)" % (plan.dish_id.name or "", plan.outlet_id.name or "", plan.date or "")

    @api.depends("date")
    def _compute_weekday(self):
        for plan in self:
            plan.weekday = str(plan.date.weekday()) if plan.date else False

    def _compute_results(self):
        Log = self.env["platewise.waste.log"]
        for plan in self:
            logs = Log.search([
                ("date", "=", plan.date), ("outlet_id", "=", plan.outlet_id.id),
                ("dish_id", "=", plan.dish_id.id), ("state", "!=", "draft"),
                ("stage", "in", ("service", "plate")),
            ]) if plan.date and plan.outlet_id and plan.dish_id else Log
            wasted = sum(logs.mapped("quantity_kg"))
            plan.wasted_kg = wasted
            plan.consumed_kg = max(plan.produced_kg - wasted, 0.0)
            plan.waste_pct = (wasted / plan.produced_kg * 100.0) if plan.produced_kg else 0.0

    @api.depends("date", "outlet_id", "dish_id", "planned_kg")
    def _compute_suggestion(self):
        for plan in self:
            plan.suggested_kg = 0.0
            plan.history_count = 0
            plan.potential_saving_aed = 0.0
            if not (plan.date and plan.outlet_id and plan.dish_id):
                continue
            base_domain = [
                ("outlet_id", "=", plan.outlet_id.id), ("dish_id", "=", plan.dish_id.id),
                ("state", "=", "done"), ("date", "<", plan.date),
            ]
            # 1) Same weekday over the last few weeks (captures weekend brunch peaks)
            history = self.search(base_domain + [("weekday", "=", str(plan.date.weekday()))],
                                  order="date desc", limit=HISTORY_WEEKS)
            # 2) Fall back to the last 7 days if this weekday has no history yet
            if not history:
                history = self.search(base_domain, order="date desc", limit=7)
            if not history:
                continue
            avg_consumed = sum(history.mapped("consumed_kg")) / len(history)
            suggestion = round(avg_consumed * SAFETY_BUFFER * 2) / 2.0   # round to 0.5 kg
            plan.suggested_kg = suggestion
            plan.history_count = len(history)
            plan.potential_saving_aed = max(plan.planned_kg - suggestion, 0.0) * plan.dish_id.cost_per_kg

    def action_apply_suggestion(self):
        for plan in self:
            if plan.state == "done":
                raise UserError("This prep plan is already done.")
            if not plan.suggested_kg:
                raise UserError("Not enough history yet to suggest a quantity for %s." % plan.dish_id.name)
            plan.planned_kg = plan.suggested_kg

    def action_mark_done(self):
        for plan in self:
            if not plan.produced_kg:
                plan.produced_kg = plan.planned_kg
            if not plan.produced_kg:
                raise UserError("Enter the produced quantity before closing the plan.")
            plan.state = "done"

    def action_view_waste_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Waste Logs",
            "res_model": "platewise.waste.log",
            "view_mode": "list,form",
            "domain": [("date", "=", self.date), ("outlet_id", "=", self.outlet_id.id), ("dish_id", "=", self.dish_id.id)],
            "context": {"default_date": self.date, "default_outlet_id": self.outlet_id.id, "default_dish_id": self.dish_id.id},
        }

    # ------------------------------------------------------------------
    # Sample data generator (for demos / judging)
    # ------------------------------------------------------------------
    @api.model
    def action_generate_demo_data(self):
        Outlet = self.env["platewise.outlet"]
        if Outlet.search_count([("name", "=", "Saffron Buffet")]):
            raise UserError("Sample data has already been generated.")
        rng = random.Random(42)
        ref = self.env.ref
        cat = {
            "red_meat": ref("platewise.category_red_meat"), "poultry": ref("platewise.category_poultry"),
            "seafood": ref("platewise.category_seafood"), "dairy": ref("platewise.category_dairy"),
            "rice": ref("platewise.category_rice_grains"), "bakery": ref("platewise.category_bakery"),
            "veg": ref("platewise.category_fruit_veg"), "dessert": ref("platewise.category_desserts"),
        }
        Dish = self.env["platewise.dish"]

        def dish(name, category, cost):
            return Dish.create({"name": name, "category_id": cat[category].id, "cost_per_kg": cost})

        d = {
            "lamb": dish("Lamb Ouzi", "red_meat", 68.0),
            "biryani": dish("Chicken Biryani", "rice", 26.0),
            "hammour": dish("Grilled Hammour", "seafood", 85.0),
            "hummus": dish("Hummus & Mezze", "veg", 18.0),
            "fruit": dish("Fresh Fruit Platter", "veg", 22.0),
            "croissant": dish("Butter Croissants", "bakery", 30.0),
            "umali": dish("Umm Ali", "dessert", 24.0),
            "cheese": dish("Cheese Selection", "dairy", 75.0),
            "shawarma": dish("Chicken Shawarma", "poultry", 32.0),
            "steak": dish("Wagyu Striploin", "red_meat", 260.0),
            "salad": dish("Fattoush Salad", "veg", 16.0),
            "kunafa": dish("Kunafa", "dessert", 28.0),
        }
        user = self.env.ref("base.user_admin", raise_if_not_found=False) or self.env.user
        outlets = [
            (Outlet.create({"name": "Saffron Buffet", "outlet_type": "buffet", "daily_budget_aed": 900.0, "manager_id": user.id, "color": 3}),
             {"lamb": 14, "biryani": 22, "hammour": 9, "hummus": 12, "fruit": 15, "croissant": 8, "umali": 10, "cheese": 5},
             1.35),
            (Outlet.create({"name": "Azure Grill", "outlet_type": "a_la_carte", "daily_budget_aed": 400.0, "manager_id": user.id, "color": 4}),
             {"steak": 6, "hammour": 5, "salad": 4, "kunafa": 3},
             1.15),
            (Outlet.create({"name": "Grand Ballroom Banquets", "outlet_type": "banquet", "daily_budget_aed": 1200.0, "manager_id": user.id, "color": 9}),
             {"shawarma": 18, "biryani": 25, "salad": 10, "kunafa": 9, "fruit": 12},
             1.45),
        ]
        weekday_factor = {0: 0.9, 1: 0.85, 2: 0.9, 3: 1.05, 4: 1.5, 5: 1.35, 6: 0.95}  # Fri/Sat brunch peak
        Log = self.env["platewise.waste.log"]
        today = fields.Date.context_today(self)
        plans, logs = [], []
        for days_ago in range(28, 0, -1):
            day = today - timedelta(days=days_ago)
            for outlet, menu, overprod in outlets:
                for key, base in menu.items():
                    demand = base * weekday_factor[day.weekday()] * rng.uniform(0.85, 1.15)
                    # Chefs prepare the same "habit" quantity every day, ignoring the weekday pattern
                    produced = round(base * overprod * rng.uniform(0.95, 1.08), 1)
                    leftover = max(produced - demand, 0.3)
                    plans.append({"date": day, "outlet_id": outlet.id, "dish_id": d[key].id,
                                  "planned_kg": produced, "produced_kg": produced, "state": "done"})
                    plate = round(leftover * rng.uniform(0.2, 0.35), 2)
                    over = round(leftover - plate, 2)
                    shift = "breakfast" if key in ("croissant", "fruit", "cheese") else rng.choice(["lunch", "dinner"])
                    if over > 0:
                        logs.append({"date": day, "outlet_id": outlet.id, "dish_id": d[key].id, "stage": "service",
                                     "reason": "overproduction", "quantity_kg": over, "shift": shift, "state": "confirmed"})
                    if plate > 0:
                        logs.append({"date": day, "outlet_id": outlet.id, "dish_id": d[key].id, "stage": "plate",
                                     "reason": "plate_waste", "quantity_kg": plate, "shift": shift, "state": "confirmed"})
                # Occasional storage losses
                if rng.random() < 0.3:
                    key = rng.choice(list(menu))
                    logs.append({"date": day, "outlet_id": outlet.id, "dish_id": d[key].id, "stage": "storage",
                                 "reason": rng.choice(["spoilage", "expired"]), "quantity_kg": round(rng.uniform(0.5, 3.0), 2),
                                 "shift": "night", "state": "confirmed"})
                if rng.random() < 0.4:
                    key = rng.choice(list(menu))
                    logs.append({"date": day, "outlet_id": outlet.id, "dish_id": d[key].id, "stage": "prep",
                                 "reason": "trim", "quantity_kg": round(rng.uniform(0.3, 1.5), 2),
                                 "shift": "breakfast", "state": "confirmed"})
        # Today and tomorrow: plans still at "habit" quantities -> PlateWise suggestions show the saving
        for days_ahead in (0, 1):
            day = today + timedelta(days=days_ahead)
            for outlet, menu, overprod in outlets:
                for key, base in menu.items():
                    plans.append({"date": day, "outlet_id": outlet.id, "dish_id": d[key].id,
                                  "planned_kg": round(base * overprod, 1), "state": "planned"})
        self.create(plans)
        Log.create(logs)
        # A couple of fresh draft entries from this morning's shift
        Log.create([
            {"outlet_id": outlets[0][0].id, "dish_id": d["croissant"].id, "stage": "service", "reason": "overproduction",
             "quantity_kg": 1.8, "shift": "breakfast", "note": "Late check-outs, low breakfast covers."},
            {"outlet_id": outlets[0][0].id, "dish_id": d["fruit"].id, "stage": "plate", "reason": "plate_waste",
             "quantity_kg": 0.9, "shift": "breakfast"},
        ])
        Outlet._cron_check_waste_budget()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": "PlateWise", "message": "Sample hotel data generated: 3 outlets, 12 dishes, 4 weeks of history.",
                       "type": "success", "sticky": False,
                       "next": self.env["ir.actions.act_window"]._for_xml_id("platewise.platewise_outlet_dashboard_action")},
        }
