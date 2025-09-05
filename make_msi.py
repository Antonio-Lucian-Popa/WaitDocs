# make_msi.py v2 — WiX v6, fără harvest, cu <Files> + -bindpath + EmbedCab
import argparse, os, shutil, subprocess, sys, uuid
from pathlib import Path

def find_wix():
    c = Path(os.environ["USERPROFILE"]) / ".dotnet" / "tools" / "wix.exe"
    return str(c) if c.is_file() else None

def run(cmd):
    print(f"\n>>> {cmd}\n")
    subprocess.check_call(cmd, shell=True)

def write_product_wxs(path, *, app_name, manufacturer, version, upgrade_code,
                      scope, icon_path, exe_name):
    per_machine = (scope.lower() == "permachine")

    dir_block = (f"""
    <StandardDirectory Id="ProgramFilesFolder">
      <Directory Id="INSTALLDIR_COMPANY" Name="{manufacturer}">
        <Directory Id="INSTALLDIR" Name="{app_name}" />
      </Directory>
    </StandardDirectory>""" if per_machine else f"""
    <StandardDirectory Id="LocalAppDataFolder">
      <Directory Id="INSTALLDIR_COMPANY" Name="{manufacturer}">
        <Directory Id="INSTALLDIR" Name="{app_name}" />
      </Directory>
    </StandardDirectory>""")

    icon_nodes = (f"""
    <Icon Id="AppIcon.ico" SourceFile="{icon_path}" />
    <Property Id="ARPPRODUCTICON" Value="AppIcon.ico"/>""" if icon_path else "")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs"
     xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui">
  <Package Name="{app_name}"
           Manufacturer="{manufacturer}"
           Version="{version}"
           UpgradeCode="{upgrade_code}"
           InstallerVersion="500"
           Scope="{ 'perMachine' if per_machine else 'perUser' }"
           Compressed="yes"
           Language="1033">

    <!-- ✅ Încorporează cabinetul în MSI (un singur fișier) -->
    <MediaTemplate EmbedCab="yes" />

{icon_nodes}
{dir_block}

    <StandardDirectory Id="ProgramMenuFolder">
      <Directory Id="ProgramMenuDir" Name="{app_name}" />
    </StandardDirectory>
    <StandardDirectory Id="DesktopFolder" />

    <Feature Id="MainFeature" Title="{app_name}" Level="1">
      <!-- Include toate fișierele din bindpath 'App' -->
      <Files Directory="INSTALLDIR" Include="!(bindpath.App)\\**" />
      <ComponentRef Id="Shortcuts" />
    </Feature>

    <Component Id="Shortcuts" Guid="*">
      <Shortcut Id="StartMenuShortcut" Name="{app_name}"
                Target="[INSTALLDIR]{exe_name}" WorkingDirectory="INSTALLDIR"
                Directory="ProgramMenuDir" />
      <Shortcut Id="DesktopShortcut" Name="{app_name}"
                Target="[INSTALLDIR]{exe_name}" WorkingDirectory="INSTALLDIR"
                Directory="DesktopFolder" />
      <RemoveFolder Id="ProgramMenuDirRemove" Directory="ProgramMenuDir" On="uninstall"/>
      <RegistryValue Root="HKCU" Key="Software\\{manufacturer}\\{app_name}"
                     Name="installed" Type="integer" Value="1" KeyPath="yes"/>
    </Component>

    <!-- UI standard pentru alegerea folderului -->
    <ui:WixUI Id="WixUI_InstallDir" />
    <Property Id="WIXUI_INSTALLDIR" Value="INSTALLDIR" />
  </Package>
</Wix>
"""
    path.write_text(xml, encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--exe-name", required=True)
    ap.add_argument("--app-name", required=True)
    ap.add_argument("--manufacturer", required=True)
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--scope", choices=["perUser", "perMachine"], default="perUser")
    ap.add_argument("--icon", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--upgrade-code-file", default="installer_upgrade_code.txt")
    args = ap.parse_args()

    wix = find_wix()
    if not wix:
        print("Eroare: nu am găsit wix.exe (dotnet tool). Rulează: dotnet tool install --global wix")
        sys.exit(1)

    app_dir = Path(args.app_dir).resolve()
    if not app_dir.is_dir():
        print(f"Eroare: folderul nu există: {app_dir}")
        sys.exit(1)
    if not (app_dir / args.exe_name).is_file():
        print(f"Eroare: executabilul nu există: {app_dir / args.exe_name}")
        sys.exit(1)

    work = Path("installer_build").resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    ug_file = Path(args.upgrade_code_file)
    if ug_file.exists():
        upgrade_code = ug_file.read_text(encoding="utf-8").strip()
    else:
        upgrade_code = "{" + str(uuid.uuid4()).upper() + "}"
        ug_file.write_text(upgrade_code, encoding="utf-8")

    product = work / "Product.wxs"
    write_product_wxs(
        product,
        app_name=args.app_name,
        manufacturer=args.manufacturer,
        version=args.version,
        upgrade_code=upgrade_code,
        scope=args.scope,
        icon_path=args.icon.replace("/", "\\") if args.icon else "",
        exe_name=args.exe_name
    )

    # Extensii UI/Util (idempotent)
    run(f"\"{wix}\" extension add WixToolset.UI.wixext")
    run(f"\"{wix}\" extension add WixToolset.Util.wixext")

    out_name = args.out or f"{args.app_name}-{args.version}.msi"
    out_path = Path(out_name).resolve()

    # IMPORTANT: sursa fișierelor vine prin -bindpath App="<dist/WaitDocs>"
    run(
        f"\"{wix}\" build \"{product}\" "
        f"-ext WixToolset.UI.wixext -ext WixToolset.Util.wixext "
        f"-bindpath App=\"{app_dir}\" "
        f"-o \"{out_path}\""
    )

    print("\n✅ Gata!")
    print(f"MSI: {out_path}")
    print(f"UpgradeCode: {upgrade_code}")
    print("Notă: pentru perMachine va cere admin.")

if __name__ == "__main__":
    main()
