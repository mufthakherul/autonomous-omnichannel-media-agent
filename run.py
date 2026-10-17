import argparse
import uvicorn
from app.database import init_db
from app.services.agent_worker import agent_worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous YouTube AI Agent & Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Dashboard server port (default: 8000)")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single autonomous pipeline cycle from CLI and exit without starting the web server",
    )
    args = parser.parse_args()

    init_db()
    if args.once:
        result = agent_worker.run_single_cycle_sync()
        print("Autonomous Cycle Result:", result)
        return

    print(f"Starting Autonomous YouTube AI Agent Dashboard at http://{args.host}:{args.port}")
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
