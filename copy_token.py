"""Copy the connector token locally without displaying it. Run by the user only."""
import os
import subprocess
from serve import load
if __name__=='__main__':
    load()
    subprocess.run(['/usr/bin/pbcopy'],input=os.environ['BRIDGE_TOKEN'].encode(),check=True)
    print('Bridge token copied. Paste only into Muse secure credential input.')
