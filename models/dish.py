from odoo import api, models, fields


class PlatewiseDish(models.Model):
    _name = "platewise.dish"
    _description = "Dish / Menu Item"
    _order = "name"

    name = fields.Char(string="Dish", required=True)
    image = fields.Image(max_width=512, max_height=512)
    active = fields.Boolean(default=True)
    category_id = fields.Many2one("platewise.food.category", string="Food Category", required=True)
    product_id = fields.Many2one(
        "product.product", string="Linked Product",
        help="Optional: link to the Odoo product so the cost per kg follows the product cost.",
    )
    cost_per_kg = fields.Float(string="Cost per kg (AED)", digits=(16, 2), required=True)
    co2_factor = fields.Float(related="category_id.co2_factor", string="kg CO2e per kg")
    waste_log_ids = fields.One2many("platewise.waste.log", "dish_id", string="Waste Logs")
    total_waste_kg = fields.Float(string="Total Wasted (kg)", compute="_compute_waste_totals")
    total_waste_cost = fields.Float(string="Total Loss (AED)", compute="_compute_waste_totals")

    _check_cost_per_kg = models.Constraint("CHECK(cost_per_kg >= 0)", "Cost per kg cannot be negative!")

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for dish in self:
            if dish.product_id and dish.product_id.standard_price:
                dish.cost_per_kg = dish.product_id.standard_price

    @api.depends("waste_log_ids.quantity_kg", "waste_log_ids.cost_aed")
    def _compute_waste_totals(self):
        for dish in self:
            dish.total_waste_kg = sum(dish.waste_log_ids.mapped("quantity_kg"))
            dish.total_waste_cost = sum(dish.waste_log_ids.mapped("cost_aed"))
