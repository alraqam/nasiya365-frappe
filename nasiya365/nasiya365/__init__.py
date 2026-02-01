"""
Nasiya365 - BNPL SaaS Platform
Buy Now, Pay Later with Inventory & Sales Management
"""

__version__ = "0.0.1"

import sys
# Hack to support legacy/redundant module path required by Frappe
# when app_name == module_name
sys.modules["nasiya365.nasiya365"] = sys.modules["nasiya365"]
