from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

setup(
    name="nasiya365",
    version="0.0.1",
    description="BNPL SaaS Platform - Buy Now Pay Later with Inventory & Sales Management",
    author="Nasiya365 Team",
    author_email="info@nasiya365.uz",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
