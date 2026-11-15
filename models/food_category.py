from odoo import models, fields


class PlatewiseFoodCategory(models.Model):
    _name = "platewise.food.category"
    _description = "Food Category"
    _order = "name"

    name = fields.Char(string="Food Category", required=True)
    co2_factor = fields.Float(
        string="kg CO2e per kg wasted",
        digits=(16, 2),
        help="Indicative lifecycle emissions per kg of food wasted. "
             "Adjust to match your sustainability reporting methodology.",
    )
    color = fields.Integer()
    dish_ids = fields.One2many("platewise.dish", "category_id", string="Dishes")

    _unique_category_name = models.Constraint("UNIQUE(name)", "Food categories must be unique")
    _check_co2_factor = models.Constraint("CHECK(co2_factor >= 0)", "The CO2e factor cannot be negative!")
