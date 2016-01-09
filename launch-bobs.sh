sleep 60
echo "Launching BOBS. Use screen -r to view."
cd /home/pi/BOBS-Pancreas
screen -dmS bash sudo python bobs-pancreas.py
cd /
