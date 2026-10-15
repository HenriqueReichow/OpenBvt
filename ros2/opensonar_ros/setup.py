from glob import glob

from setuptools import setup

package = "opensonar_ros"
setup(
    name=package,
    version="4.1.0",
    packages=[package],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package}"]),
        (f"share/{package}", ["package.xml"]),
        (f"share/{package}/launch", glob("launch/*.py")),
        (f"share/{package}/config", glob("config/*")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Henrique Reichow",
    maintainer_email="reichowhenrique@gmail.com",
    description="ROS 2 node for P900 and M900 sonars (client of the opensonar service)",
    license="MIT",
    tests_require=["pytest"],
    entry_points={"console_scripts": [f"sonar_node = {package}.sonar_node:main"]},
)
