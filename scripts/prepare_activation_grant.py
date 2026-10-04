"""Retired operational-password activation entry point.

Historical grants remain in the database. Issue expiring one-time codes in
Control Center /ativacoes; this command deliberately performs no I/O.
"""
def main():
    raise SystemExit("Activation by operational password is disabled. Use Control Center > Activation codes.")

if __name__ == "__main__":
    main()
