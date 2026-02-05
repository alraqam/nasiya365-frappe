from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

# Explicitly include nested nasiya365.nasiya365 (required by Frappe sync when module name == app name)
packages = find_packages()
if "nasiya365.nasiya365" not in packages:
    packages.append("nasiya365.nasiya365")

setup(
    name="nasiya365",
    version="0.0.1",
    description="BNPL SaaS Platform - Buy Now Pay Later with Inventory & Sales Management",
    author="Nasiya365 Team",
    author_email="info@nasiya365.uz",
    packages=packages,
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
