import pefile, os

dlls = [
    'Microsoft.WindowsAppRuntime.dll',
    'MRM.dll',
]
base = r'C:\Users\User\source\repos\CarDiagnosticApp\bin\Release\net10.0-windows10.0.19041.0\win-x64\publish'
for dll in dlls:
    path = os.path.join(base, dll)
    if os.path.exists(path):
        pe = pefile.PE(path)
        print(f'{dll}:')
        print(f'  Entry point RVA: 0x{pe.OPTIONAL_HEADER.AddressOfEntryPoint:X}')
        # Check exports for DllMain or DllGetActivationFactory
        if hasattr(pe, 'DIRECTORY_ENTRY_EXPORT'):
            exports = pe.DIRECTORY_ENTRY_EXPORT.symbols
            for exp in exports:
                name = exp.name.decode() if exp.name else '(ordinal)'
                if 'DllMain' in name or 'DllGet' in name or 'DllCan' in name:
                    print(f'  Export: {name}')
