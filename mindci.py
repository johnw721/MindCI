import sys
import subprocess

def run_command(name, command):
    print(f"\n🚀 {name}")
    result = subprocess.run(command, shell=True)

    if result.returncode != 0:
        print(f"❌ Failed: {name}")
        exit(1)

    print(f"✅ Done: {name}")

def run_pipeline():
    run_command("Convert Notes → JSON", "python convert.py")
    run_command("Generate Questions + Anki", "python generate.py")

def convert_only():
    run_command("Convert Notes → JSON", "python convert.py")

def generate_only():
    run_command("Generate Questions + Anki", "python generate.py")

def help_menu():
    print("""
MindCI Commands:

run        → full pipeline
convert    → raw → JSON
generate   → JSON → questions + Anki
help       → show this menu
""")

def main():
    if len(sys.argv) < 2:
        help_menu()
        return

    command = sys.argv[1]

    if command == "run":
        run_pipeline()
    elif command == "convert":
        convert_only()
    elif command == "generate":
        generate_only()
    elif command == "analyze":
        run_command("JD Gap Analysis", "python jd_analyze.py")
    else:
        help_menu()

if __name__ == "__main__":
    main()