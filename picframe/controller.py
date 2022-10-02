"""Controller of picframe."""

import logging
import time
import json
import os
import signal
import sys
from picframe.interface_peripherals import InterfacePeripherals


def make_date(txt):
    dt = txt.replace('/', ':').replace('-', ':').replace(',', ':').replace('.', ':').split(':')
    dt_tuple = tuple(int(i) for i in dt)  # TODO catch badly formed dates?
    return time.mktime(dt_tuple + (0, 0, 0, 0, 0, 0))


class Controller:
    """Controller of picframe.

    This controller interacts via mqtt with the user to steer the image display.

    Attributes
    ----------
    model : Model
        model of picframe containing config and business logic
    viewer : ViewerDisplay
        viewer of picframe representing the display


    Methods
    -------
    paused
        Getter and setter for pausing image display.
    next
        Show next image.
    back
        Show previous image.

    """

    def __init__(self, model, viewer, tex_provider):
        self.__logger = logging.getLogger("controller.Controller")
        self.__logger.info('creating an instance of Controller')
        self.__model = model
        self.__tex_provider = tex_provider
        self.__viewer = viewer
        self.__paused = False
        self.__force_navigate = False
        self.__next_tm = 0
        self.__date_from = make_date(
            '1901/12/15')  # TODO This seems to be the minimum date to be handled by date functions
        self.__date_to = make_date('2038/1/1')
        self.__location_filter = ""
        self.__where_clauses = {}
        self.__sort_clause = "exif_datetime ASC"
        self.publish_state = lambda x, y: None
        self.keep_looping = True
        self.__location_filter = ''
        self.__tags_filter = ''
        self.__interface_peripherals = None
        self.__shutdown_complete = False

    @property
    def paused(self):
        """Get or set the current state for pausing image display. Setting paused to true
        will show the actual image as long paused is not set to false.
        """
        return self.__paused

    @paused.setter
    def paused(self, val: bool):
        self.__paused = val
        pic = self.__model.get_current_pics()[0]  # only refresh left text
        self.__viewer.reset_name_tm(pic, val, side=0, pair=self.__model.get_current_pics()[1] is not None)
        self.publish_state()

    def next(self):
        self.__next_tm = 0
        self.__viewer.reset_name_tm()
        self.__force_navigate = True

    def back(self):
        self.__model.set_next_file_to_previous_file()
        self.__next_tm = 0
        self.__viewer.reset_name_tm()
        self.__force_navigate = True

    def delete(self):
        self.__model.delete_file()
        self.next()  # TODO check needed to avoid skipping one as record has been deleted from model.__file_list
        self.__next_tm = 0

    def set_show_text(self, txt_key=None, val="ON"):
        if val is True:  # allow to be called with boolean from httpserver
            val = "ON"
        self.__viewer.set_show_text(txt_key, val)
        for (side, pic) in enumerate(self.__model.get_current_pics()):
            if pic is not None:
                self.__viewer.reset_name_tm(pic, self.paused, side, self.__model.get_current_pics()[1] is not None)

    def refresh_show_text(self):
        for (side, pic) in enumerate(self.__model.get_current_pics()):
            if pic is not None:
                self.__viewer.reset_name_tm(pic, self.paused, side, self.__model.get_current_pics()[1] is not None)

    def purge_files(self):
        self.__model.purge_files()

    @property
    def subdirectory(self):
        return self.__model.subdirectory

    @subdirectory.setter
    def subdirectory(self, dir):
        self.__model.subdirectory = dir
        self.__model.force_reload()
        self.__next_tm = 0

    @property
    def date_from(self):
        return self.__date_from

    @date_from.setter
    def date_from(self, val):
        try:
            self.__date_from = float(val)
        except ValueError:
            self.__date_from = make_date(val if len(val) > 0 else '1901/12/15')
        if len(val) > 0:
            self.__model.set_where_clause('date_from', "exif_datetime > {:.0f}".format(self.__date_from))
        else:
            self.__model.set_where_clause('date_from')  # remove from where_clause
        self.__model.force_reload()
        self.__next_tm = 0

    @property
    def date_to(self):
        return self.__date_to

    @date_to.setter
    def date_to(self, val):
        try:
            self.__date_to = float(val)
        except ValueError:
            self.__date_to = make_date(val if len(val) > 0 else '2038/1/1')
        if len(val) > 0:
            self.__model.set_where_clause('date_to', "exif_datetime < {:.0f}".format(self.__date_to))
        else:
            self.__model.set_where_clause('date_to')  # remove from where_clause
        self.__model.force_reload()
        self.__next_tm = 0

    @property
    def display_is_on(self):
        return self.__viewer.display_is_on

    @display_is_on.setter
    def display_is_on(self, on_off):
        self.paused = not on_off
        self.__viewer.display_is_on = on_off
        self.publish_state()

    @property
    def clock_is_on(self):
        return self.__viewer.clock_is_on

    @clock_is_on.setter
    def clock_is_on(self, on_off):
        self.__viewer.clock_is_on = on_off

    @property
    def shuffle(self):
        return self.__model.shuffle

    @shuffle.setter
    def shuffle(self, val: bool):
        self.__model.shuffle = val
        self.__model.force_reload()
        self.__next_tm = 0
        self.publish_state()

    @property
    def fade_time(self):
        return self.__model.fade_time

    @fade_time.setter
    def fade_time(self, time):
        self.__model.fade_time = float(time)
        self.__next_tm = 0

    @property
    def time_delay(self):
        return self.__model.time_delay

    @time_delay.setter
    def time_delay(self, t):
        self.__model.time_delay = max(5.0, float(t))
        self.__next_tm = 0

    @property
    def brightness(self):
        return self.__viewer.get_brightness()

    @brightness.setter
    def brightness(self, val):
        self.__viewer.set_brightness(float(val))
        self.publish_state()

    @property
    def matting_images(self):
        return self.__tex_provider.get_matting_images()

    @matting_images.setter
    def matting_images(self, val):
        self.__tex_provider.set_matting_images(float(val))
        self.__next_tm = 0

    @property
    def location_filter(self):
        return self.__location_filter

    @location_filter.setter
    def location_filter(self, val):
        self.__location_filter = val
        if len(val) > 0:
            self.__model.set_where_clause("location_filter", self.__build_filter(val, "location"))
        else:
            self.__model.set_where_clause("location_filter")  # remove from where_clause
        self.__model.force_reload()
        self.__next_tm = 0

    @property
    def tags_filter(self):
        return self.__tags_filter

    @tags_filter.setter
    def tags_filter(self, val):
        self.__tags_filter = val
        if len(val) > 0:
            self.__model.set_where_clause("tags_filter", self.__build_filter(val, "tags"))
        else:
            self.__model.set_where_clause("tags_filter")  # remove from where_clause
        self.__model.force_reload()
        self.__next_tm = 0

    def __build_filter(self, val, field):
        if val.count("(") != val.count(")"):
            return None  # this should clear the filter and not raise an error
        val = val.replace(";", "").replace("'", "").replace("%", "").replace('"', '')  # SQL scrambling
        tokens = ("(", ")", "AND", "OR", "NOT")  # now copes with NOT
        val_split = val.replace("(", " ( ").replace(")", " ) ").split()  # so brackets not joined to words
        filter = []
        last_token = ""
        for s in val_split:
            s_upper = s.upper()
            if s_upper in tokens:
                if s_upper in ("AND", "OR"):
                    if last_token in ("AND", "OR"):
                        return None  # must have a non-token between
                    last_token = s_upper
                filter.append(s)
            else:
                if last_token is not None:
                    filter.append("{} LIKE '%{}%'".format(field, s))
                else:
                    filter[-1] = filter[-1].replace("%'", " {}%'".format(s))
                last_token = None
        return "({})".format(" ".join(filter))  # if OR outside brackets will modify the logic of rest of where clauses

    def text_is_on(self, txt_key):
        return self.__viewer.text_is_on(txt_key)

    def get_number_of_files(self):
        return self.__model.get_number_of_files()

    def get_directory_list(self):
        actual_dir, dir_list = self.__model.get_directory_list()
        return actual_dir, dir_list

    def get_current_path(self):
        (pic, _) = self.__model.get_current_pics()
        return pic.fname

    def loop(self):  # TODO exit loop gracefully and call image_cache.stop()
        # catch ctrl-c
        signal.signal(signal.SIGINT, self.__signal_handler)

        # next_check_tm = time.time() + self.__model.get_model_config()['check_dir_tm']
        while self.keep_looping:

            # if self.__next_tm == 0: #TODO double check why these were set when next_tm == 0
            #    time_delay = 1 # must not be 0
            #    fade_time = 1 # must not be 0
            # else:
            time_delay = self.__model.time_delay
            fade_time = self.__model.fade_time

            tm = time.time()
            if not self.paused and tm > self.__next_tm or self.__force_navigate:
                self.__logger.info("It's time to look for a new picture")
                (image_attr, pics, new_sfg) = self.__tex_provider.consume()
                if pics:
                    # DBNote: clear the things that caused you to enter
                    self.__logger.info("We found a picture!")
                    self.__next_tm = tm + self.__model.time_delay
                    self.__force_navigate = False
                    self.__viewer.switch_image(pics, new_sfg, time_delay, fade_time, self.__paused)
                    # DBNote: this should only happen when we push to the viewer
                    self.publish_state(pics[0].fname, image_attr)

            self.__model.pause_looping = self.__viewer.is_in_transition()
            (loop_running, skip_image) = self.__viewer.slideshow_is_running(time_delay, fade_time, self.__paused)
            if not loop_running:
                break
            if skip_image:
                self.__next_tm = 0
            self.__interface_peripherals.check_input()
        self.__shutdown_complete = True

    def start(self):
        self.__viewer.slideshow_start()
        self.__tex_provider.set_display(self.__viewer.display_width, self.__viewer.display_height)
        self.__interface_peripherals = InterfacePeripherals(self.__model, self.__viewer, self)

    def stop(self):
        self.keep_looping = False
        self.__interface_peripherals.stop()
        while not self.__shutdown_complete:
            time.sleep(0.05)  # block until main loop has stopped
        self.__model.stop_image_chache()  # close db tidily (blocks till closed)
        self.__viewer.slideshow_stop()  # do this last

    def __signal_handler(self, sig, frame):
        print('You pressed Ctrl-c!')
        self.__shutdown_complete = True
        self.stop()
