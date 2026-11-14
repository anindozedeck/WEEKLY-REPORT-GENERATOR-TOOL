# Weekly Report Generator

**Client:** Any business that keeps daily sales or operations data in CSV files.  
**Setup price range:** KES 20,000-35,000 (one-time; agree the final price with your provider).

This tool combines the CSV files in one folder, calculates totals, asks Anthropic AI for a short comment based on those totals, and emails a weekly report with the merged CSV attached. After setup, Windows runs it automatically on the schedule you choose.

## What you need

- A Windows computer that is on and connected to the internet when the report is due.
- Python 3.10 or newer installed.
- Daily CSV files with a heading row (the column names) saved in the `input` folder.
- An email account that allows SMTP sending, its SMTP settings, and an Anthropic API key.

The AI receives the row count and up to 20 numeric column totals only. It does not receive the original CSV rows. If the AI service is unavailable, the email can still be sent with a clear note that the AI comment is missing. Email settings must be working for a report to be delivered.

## One-time setup

1. Install Python from [python.org](https://www.python.org/downloads/). During installation, select **Add python.exe to PATH**.
2. Keep `Weekly_Report_Generator.py`, `run_weekly_report.bat`, and `.env` together in the same folder.
3. Create a folder named `input` next to those files. Put each day's CSV file in `input`. Use one header row in every file. The files are combined by column name, so the headings should be consistent.
4. Open `.env` with Notepad and add the email settings below. Keep the existing `ANTHROPIC_API_KEY` line; replace only its value if you need to use a different key. Do not share or email this file.

   ```text
   ANTHROPIC_API_KEY=your_anthropic_api_key
   ANTHROPIC_MODEL=claude-haiku-4-5-20251001
   SMTP_HOST=smtp.your-email-provider.com
   SMTP_PORT=587
   SMTP_USER=your-email@example.com
   SMTP_PASSWORD=your-email-password-or-app-password
   SMTP_USE_TLS=true
   SMTP_USE_SSL=false
   REPORT_SENDER=your-email@example.com
   REPORT_RECIPIENT=the-person-who-gets-the-report@example.com
   ```

   Your email provider may require an **app password** rather than your normal sign-in password. Ask your email provider or IT support for its SMTP host, port, and secure-mail settings. For an SSL-only provider, set `SMTP_USE_TLS=false`, `SMTP_USE_SSL=true`, and use the port it gave you (often 465). Do not set both TLS and SSL to true.

5. Optional: if you want totals only for particular columns, add a line such as `TOTAL_COLUMNS=Sales,Quantity`. The names must exactly match CSV headings. Without this setting, the program totals numeric columns and skips common ID, phone, and postal-code columns.
6. To test the setup, double-click `run_weekly_report.bat`. It sends a real report for the CSV files currently in `input`. Check the recipient's inbox and the `logs` folder.

## Schedule it once

1. Search Windows for **Task Scheduler** and open it.
2. Choose **Create Basic Task**. Name it `Weekly Report Generator`.
3. Choose **Weekly**, then select the day and time the report should be sent.
4. Choose **Start a program**. Browse to and select `run_weekly_report.bat`.
5. Finish the wizard. In Task Scheduler, open the task's properties and select **Run whether user is logged on or not** if you want it to run while you are signed out. The computer must still be on and connected to the internet.

Each successful run emails all readable CSV files in `input` and then moves them, together with the merged CSV, into `archive` in a dated folder. This prevents those files from being included again on the next scheduled run. Files that cannot be read are left in `input`. If email sending fails, source files are left in `input` so the next run can try again.

## Folders and settings

- `input`: add the daily CSV files here.
- `archive`: successfully processed source files and the merged CSV.
- `logs`: run status and readable error messages. Check this folder if a report does not arrive.
- `.env.example`: names and examples of available settings. The private `.env` file is not included in the example.

Optional settings:

| Setting | Default | Purpose |
| --- | --- | --- |
| `INPUT_FOLDER` | `input` | Folder containing source CSVs, relative to this program folder. |
| `ARCHIVE_FOLDER` | `archive` | Folder for successfully processed files. |
| `LOG_FOLDER` | `logs` | Folder for the run log. |
| `TOTAL_COLUMNS` | Automatic | Comma-separated list of exact headings to total. |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Anthropic model used for the trend comment. |

The program uses only Python's built-in libraries; no separate package installation is needed.
