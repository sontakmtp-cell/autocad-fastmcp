[app]
title = KythuatvangAutoCADAgent
project_dir = .
input_file = launcher.py
exec_directory = .
project_file =
icon =

[python]
python_path = .venv/Scripts/python.exe
packages = Nuitka==2.8.9

[qt]
qml_files =
excluded_qml_plugins =
modules = Core,Gui,Widgets
plugins = iconengines,imageformats,platforminputcontexts,platforms,styles

[android]
wheel_pyside =
wheel_shiboken =
plugins =

[nuitka]
macos.permissions =
mode = standalone
extra_args = --mingw64 --assume-yes-for-downloads --windows-console-mode=disable --quiet --noinclude-qt-translations

[buildozer]
mode = debug
recipe_dir =
jars_dir =
ndk_path =
sdk_path =
local_libs =
arch =
