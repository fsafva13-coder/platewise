{
    'name': "PlateWise",
    'summary': "Food-waste intelligence for hotel & restaurant kitchens",
    'description': """
PlateWise turns kitchen food waste into a visible cost line.
Staff log waste in seconds, every entry is priced in AED and CO2e,
prep plans are suggested from consumption history, and managers
are alerted when an outlet goes over its daily waste budget.
    """,
    'version': '19.0.1.0.0',
    'category': 'Hospitality',
    'author': "Team PlateWise",
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'product'],
    'data': [
        "security/ir.model.access.csv",
        "data/platewise_data.xml",
        "report/outlet_report.xml",
        "views/food_category_views.xml",
        "views/dish_views.xml",
        "views/outlet_views.xml",
        "views/waste_log_views.xml",
        "views/prep_plan_views.xml",
        "views/platewise_menus.xml",
    ],
    'demo': [
        "data/platewise_demo.xml",
    ],
    'application': True,
    'installable': True,
}
