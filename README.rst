=====
NOTES
=====

To pack JPEGs to and MJPEG avi::

   python make_avi.py --output mjpeg.avi [dirnames]

To encode MJPEG to h264 try::

   mencoder -profile h264-vhq -x264encopts crf=26 mjpeg.avi -o h264.avi

To repackage as Matroska::

   mkvmerge -o output.mkv h264.avi
