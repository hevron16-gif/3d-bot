import pefile, os

dlls = [
    'Microsoft.WindowsAppRuntime.dll',
    'Microsoft.WindowsAppRuntime.Bootstrap.dll',
    'MRM.dll',
    'DWriteCore.dll',
]
base = r'C:\Users\User\source\repos\CarDiagnosticApp\bin\Release\net10.0-windows10.0.19041.0\win-x64\publish'
for dll in dlls:
    path = os.path.join(base, dll)
    if os.path.exists(path):
        try:
            pe = pefile.PE(path)
            print(f'\n=== {dll} imports ===')
            if hasattr(pe, 'DIRECTORY_ENTRY_IMPORT'):
                for entry in pe.DIRECTORY_ENTRY_IMPORT:
                    name = entry.dll.decode() if isinstance(entry.dll, bytes) else entry.dll
                    print(f'  {name}')
        except Exception as e:
            print(f'  ERROR: {e}')
    else:
        print(f'\nMISSING: {dll}')
