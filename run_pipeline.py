import subprocess

def run_step(name, command):
    print(f"\n🚀 {name}")
    result = subprocess.run(command, shell=True)

    if result.returncode != 0:
        print(f"❌ Failed: {name}")
        exit(1)

    print(f"✅ Done: {name}")

def main():
    run_step("Convert Notes → JSON", "python convert.py")
    run_step("Generate Questions", "python generate.py")

    print("\n🎉 Pipeline complete!")
    print("→ Check output/questions.md")

if __name__ == "__main__":
    main()