@echo off
title Push NSE Stocks Intraday Bot to GitHub
echo ==============================================================================
echo           PUSHING NSE STOCKS INTRADAY BOT TO GITHUB
echo ==============================================================================
echo.
echo Target Repo: https://github.com/rajashu1217-glitch/nse-stocks-intraday-bot.git
echo (Make sure you have created an empty repo named 'nse-stocks-intraday-bot' on GitHub first!)
echo.
git push -u origin main
echo.
echo ==============================================================================
echo Done! Next step on Render:
echo   1. Go to https://dashboard.render.com -^> New -^> Blueprint (or Web Service)
echo   2. Connect 'nse-stocks-intraday-bot'
echo   3. Add env vars TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
echo ==============================================================================
pause
