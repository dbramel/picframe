# for development
import sys
sys.path.insert(1, "/home/pi/dev/pi3d") #TODO just for debugging when not properly installed
import pi3d
from pi3d.Texture import MAX_SIZE
import math
import time
import subprocess
import logging
import os
import numpy as np
from PIL import Image, ImageFilter
from picframe import mat_image

# utility functions with no dependency on ViewerDisplay properties
def txt_to_bit(txt):
    txt_map = {"title":1, "caption":2, "name":4, "date":8, "location":16, "folder":32}
    if txt in txt_map:
        return txt_map[txt]
    return 0

def parse_show_text(txt):
    show_text = 0
    txt = txt.lower()
    for txt_key in ("title", "caption", "name", "date", "location", "folder"):
        if txt_key in txt:
            show_text |= txt_to_bit(txt_key)
    return show_text

class ViewerDisplay:

    def __init__(self, config):
        self.__logger = logging.getLogger("viewer_display.ViewerDisplay")
        self.__blur_amount = config['blur_amount']
        self.__blur_zoom = config['blur_zoom']
        self.__blur_edges = config['blur_edges']
        self.__edge_alpha = config['edge_alpha']

        self.__mat_images = config['mat_images']
        self.__mat_portraits_only = config['mat_portraits_only']
        self.__mat_type = config['mat_type']
        self.__outer_mat_color = config['outer_mat_color']
        self.__inner_mat_color = config['inner_mat_color']
        self.__outer_mat_border = config['outer_mat_border']
        self.__inner_mat_border = config['inner_mat_border']
        self.__use_mat_texture = config['use_mat_texture']
        self.__auto_outer_mat_color = config['auto_outer_mat_color']
        self.__auto_inner_mat_color = config['auto_inner_mat_color']
        self.__auto_select_mat_type = config['auto_select_mat_type']
        self.__mat_resource_folder = os.path.expanduser(config['mat_resource_folder'])

        self.__fps = config['fps']
        self.__background = config['background']
        self.__blend_type = {"blend":0.0, "burn":1.0, "bump":2.0}[config['blend_type']]
        self.__font_file = os.path.expanduser(config['font_file'])
        self.__shader = os.path.expanduser(config['shader'])
        self.__show_text_tm = config['show_text_tm']
        self.__show_text_fm = config['show_text_fm']
        self.__show_text_sz = config['show_text_sz']
        self.__show_text = parse_show_text(config['show_text'])
        self.__text_width = config['text_width']
        self.__fit = config['fit']
        self.__auto_resize = config['auto_resize']
        self.__kenburns = config['kenburns']
        if self.__kenburns:
            self.__kb_up = True
            self.__fit = False
            self.__blur_edges = False
        if self.__blur_zoom < 1.0:
            self.__blur_zoom = 1.0
        self.__display_x = int(config['display_x'])
        self.__display_y = int(config['display_y'])
        self.__display_w = None if config['display_w'] is None else int(config['display_w'])
        self.__display_h = None if config['display_h'] is None else int(config['display_h'])
        self.__use_glx = config['use_glx']
        self.__codepoints = config['codepoints']
        self.__alpha = 0.0 # alpha - proportion front image to back
        self.__delta_alpha = 1.0
        self.__display = None
        self.__slide = None
        self.__xstep = None
        self.__ystep = None
        self.__text = None
        self.__textblock = None
        self.__text_bkg = None
        self.__sfg = None # slide for background
        self.__sbg = None # slide for foreground
        self.__next_tm = 0.0
        self.__name_tm = 0.0
        self.__in_transition = False
        self.__matter = None

    @property
    def display_is_on(self):
        try: # vcgencmd only applies to raspberry pi
            cmd = ["vcgencmd", "display_power"]
            state = str(subprocess.check_output(cmd))
            if (state.find("display_power=1") != -1):
                return True
            else:
                return False
        except:
            return True

    @display_is_on.setter
    def display_is_on(self, on_off):
        try: # vcgencmd only applies to raspberry pi
            cmd = ["vcgencmd", "display_power", "0"]
            if on_off == True:
                cmd = ["vcgencmd", "display_power", "1"]
            subprocess.call(cmd)
        except:
            return None

    def set_show_text(self, txt_key=None, val="ON"):
        if txt_key is None:
            self.__show_text = 0 # no arguments signals turning all off
        else:
            bit = txt_to_bit(txt_key) # convert field name to relevant bit 1,2,4,8,16 etc
            if val == "ON":
                self.__show_text |= bit # turn it on
            else: #TODO anything else ok to turn it off?
                bits = 65535 ^ bit
                self.__show_text &= bits # turn it off

    def text_is_on(self, txt_key):
        return self.__show_text & txt_to_bit(txt_key)

    def reset_name_tm(self, pic=None, paused=None):
        # only extend i.e. if after initial fade in
        if pic is not None and paused is not None: # text needs to be refreshed
            self.__make_text(pic, paused)
        self.__name_tm = max(self.__name_tm, time.time() + self.__show_text_tm)

    def set_brightness(self, val):
        self.__slide.unif[55] = val # take immediate effect

    def get_brightness(self):
        return float("{:.2f}".format(self.__slide.unif[55])) # TODO There seems to be a rounding issue. set 0.77 get 0.7699999809265137

    def __check_heif_then_open(self, fname):
        ext = os.path.splitext(fname)[1].lower()
        if ext in ('.heif','.heic'):
            try:
                import pyheif

                heif_file = pyheif.read(fname)
                image = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data,
                                        "raw", heif_file.mode, heif_file.stride)
                return image
            except:
                self.__logger.warning("Failed attempt to convert %s \n** Have you installed pyheif? **", fname)
        else:
            return Image.open(fname)

    # Concatenate the specified images horizontally. Clip the taller
    # image to the height of the shorter image.
    def __create_image_pair(self, im1, im2):
        sep = 8 # separation between the images
        # scale widest image to same width as narrower to avoid drastic cropping on mismatched images
        if im1.width > im2.width:
            im1 = im1.resize((im2.width, int(im1.height * im2.width / im1.width)), resample=Image.BICUBIC)
        else:
            im2 = im2.resize((im1.width, int(im2.height * im1.width / im2.width)), resample=Image.BICUBIC)
        dst = Image.new('RGB', (im1.width + im2.width + sep, min(im1.height, im2.height)))
        dst.paste(im1, (0, 0))
        dst.paste(im2, (im1.width + sep, 0))
        return dst

    def __orientate_image(self, im, orientation):
        if orientation == 2:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 3:
            im = im.transpose(Image.ROTATE_180) # rotations are clockwise
        elif orientation == 4:
            im = im.transpose(Image.FLIP_TOP_BOTTOM)
        elif orientation == 5:
            im = im.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.ROTATE_90)
        elif orientation == 6:
            im = im.transpose(Image.ROTATE_270)
        elif orientation == 7:
            im = im.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.ROTATE_270)
        elif orientation == 8:
            im = im.transpose(Image.ROTATE_90)
        return im

    def __tex_load(self, pics, size=None):
        if self.__mat_images and self.__matter == None:
            self.__matter = mat_image.MatImage(
                display_size = (self.__display.width , self.__display.height),
                resource_folder=self.__mat_resource_folder,
                mat_type = self.__mat_type,
                outer_mat_color = self.__outer_mat_color,
                inner_mat_color = self.__inner_mat_color,
                outer_mat_border = self.__outer_mat_border,
                inner_mat_border = self.__inner_mat_border,
                use_mat_texture = self.__use_mat_texture,
                auto_outer_mat_color = self.__auto_outer_mat_color,
                auto_inner_mat_color = self.__auto_inner_mat_color,
                auto_select_mat_type = self.__auto_select_mat_type)

        try:
            # Load the image(s) and correct their orientation if necessary
            if pics[0]:
                im = self.__check_heif_then_open(pics[0].fname)
                if pics[0].orientation != 1:
                     im = self.__orientate_image(im, pics[0].orientation)
            if pics[1]:
                im2 = self.__check_heif_then_open(pics[1].fname)
                if pics[1].orientation != 1:
                     im2 = self.__orientate_image(im2, pics[1].orientation)

            if self.__mat_images and (pics[0].is_portrait or not (pics[0].is_portrait or self.__mat_portraits_only)):
                if not pics[1]:
                    im = self.__matter.mat_image((im,))
                else:
                    im = self.__matter.mat_image((im, im2))
            else:
                if pics[1]: #i.e portrait pair
                    im = self.__create_image_pair(im, im2)

            (w, h) = im.size
            max_dimension = MAX_SIZE # TODO changing MAX_SIZE causes serious crash on linux laptop!
            if not self.__auto_resize: # turned off for 4K display - will cause issues on RPi before v4
                max_dimension = 3840 # TODO check if mipmapping should be turned off with this setting.
            if w > max_dimension:
                im = im.resize((max_dimension, int(h * max_dimension / w)), resample=Image.BICUBIC)
            elif h > max_dimension:
                im = im.resize((int(w * max_dimension / h), max_dimension), resample=Image.BICUBIC)
            if self.__blur_edges and size is not None:
                wh_rat = (size[0] * im.size[1]) / (size[1] * im.size[0])
                if abs(wh_rat - 1.0) > 0.01: # make a blurred background
                    (sc_b, sc_f) = (size[1] / im.size[1], size[0] / im.size[0])
                    if wh_rat > 1.0:
                        (sc_b, sc_f) = (sc_f, sc_b) # swap round
                    (w, h) =  (round(size[0] / sc_b / self.__blur_zoom), round(size[1] / sc_b / self.__blur_zoom))
                    (x, y) = (round(0.5 * (im.size[0] - w)), round(0.5 * (im.size[1] - h)))
                    box = (x, y, x + w, y + h)
                    blr_sz = (int(x * 512 / size[0]) for x in size)
                    im_b = im.resize(size, resample=0, box=box).resize(blr_sz)
                    im_b = im_b.filter(ImageFilter.GaussianBlur(self.__blur_amount))
                    im_b = im_b.resize(size, resample=Image.BICUBIC)
                    im_b.putalpha(round(255 * self.__edge_alpha))  # to apply the same EDGE_ALPHA as the no blur method.
                    im = im.resize((int(x * sc_f) for x in im.size), resample=Image.BICUBIC)
                    """resize can use Image.LANCZOS (alias for Image.ANTIALIAS) for resampling
                    for better rendering of high-contranst diagonal lines. NB downscaled large
                    images are rescaled near the start of this try block if w or h > max_dimension
                    so those lines might need changing too.
                    """
                    im_b.paste(im, box=(round(0.5 * (im_b.size[0] - im.size[0])),
                                        round(0.5 * (im_b.size[1] - im.size[1]))))
                    im = im_b # have to do this as paste applies in place
            tex = pi3d.Texture(im, blend=True, m_repeat=True, automatic_resize=self.__auto_resize,
                                free_after_load=True)
            #tex = pi3d.Texture(im, blend=True, m_repeat=True, automatic_resize=config.AUTO_RESIZE,
            #                    mipmap=config.AUTO_RESIZE, free_after_load=True) # poss try this if still some artifacts with full resolution
        except Exception as e:
            self.__logger.warning("Can't create tex from file: \"%s\" or \"%s\"", pics[0].fname, pics[1])
            self.__logger.warning("Cause: %s", e)
            tex = None
            raise
        return tex

    def __sanitize_string(self, path_name):
        name = os.path.basename(path_name)
        name = ''.join([c for c in name if c in self.__codepoints])
        return name

    def __make_text(self, pic, paused):
        # pic is just left hand pic if pics tuple has two portraits
        info_strings = []
        if self.__show_text > 0 or paused: #was SHOW_TEXT_TM > 0.0
            if (self.__show_text & 1) == 1 and pic.title is not None: # title
                info_strings.append(self.__sanitize_string(pic.title))
            if (self.__show_text & 2) == 2 and pic.caption is not None: # caption
                info_strings.append(self.__sanitize_string(pic.caption))
            if (self.__show_text & 4) == 4: # name
                info_strings.append(self.__sanitize_string(pic.fname))
            if (self.__show_text & 8) == 8 and pic.exif_datetime > 0: # date
                fdt = time.strftime(self.__show_text_fm, time.localtime(pic.exif_datetime))
                info_strings.append(fdt)
            if (self.__show_text & 16) == 16 and pic.location is not None: # location
                info_strings.append(pic.location) #TODO need to sanitize and check longer than 0 for real
            if (self.__show_text & 32) == 32: # folder
                info_strings.append(self.__sanitize_string(os.path.basename(os.path.dirname(pic.fname))))
            if paused:
                info_strings.append("PAUSED")
        final_string = " • ".join(info_strings)
        self.__textblock.set_text(text_format=final_string, wrap=self.__text_width)

        last_ch = len(final_string)
        if last_ch > 0:
            adj_y = self.__text.locations[:last_ch,1].min() + self.__display.height // 2 # y pos of last char rel to bottom of screen
            self.__textblock.set_position(y = (self.__textblock.y - adj_y + self.__show_text_sz))

    def is_in_transition(self):
        return self.__in_transition

    def slideshow_start(self):
        self.__display = pi3d.Display.create(x=self.__display_x, y=self.__display_y,
              w=self.__display_w, h=self.__display_h, frames_per_second=self.__fps,
              display_config=pi3d.DISPLAY_CONFIG_HIDE_CURSOR, background=self.__background, use_glx=self.__use_glx)
        camera = pi3d.Camera(is_3d=False)
        shader = pi3d.Shader(self.__shader)
        self.__slide = pi3d.Sprite(camera=camera, w=self.__display.width, h=self.__display.height, z=5.0)
        self.__slide.set_shader(shader)
        self.__slide.unif[47] = self.__edge_alpha
        self.__slide.unif[54] = float(self.__blend_type)
        self.__slide.unif[55] = 1.0 #brightness
        # PointText and TextBlock. If SHOW_NAMES_TM <= 0 then this is just used for no images message
        grid_size = math.ceil(len(self.__codepoints) ** 0.5)
        font = pi3d.Font(self.__font_file, codepoints=self.__codepoints, grid_size=grid_size, shadow_radius=4.0,
                        shadow=(0,0,0,128))
        self.__text = pi3d.PointText(font, camera, max_chars=200, point_size=50)
        self.__textblock = pi3d.TextBlock(x=-int(self.__display.width) * 0.5 + 50, y=-int(self.__display.height) * 0.4,
                                z=0.1, rot=0.0, char_count=199,
                                text_format="{}".format(" "), size=0.99,
                                spacing="F", space=0.02, colour=(1.0, 1.0, 1.0, 1.0))
        self.__text.add_text_block(self.__textblock)
        bkg_ht = self.__display.height // 3
        text_bkg_array = np.zeros((bkg_ht, 1, 4), dtype=np.uint8)
        text_bkg_array[:,:,3] = np.linspace(0, 170, bkg_ht).reshape(-1, 1)
        text_bkg_tex = pi3d.Texture(text_bkg_array, blend=True, mipmap=False, free_after_load=True)

        back_shader = pi3d.Shader("uv_flat")
        self.__text_bkg = pi3d.Sprite(w=self.__display.width, h=bkg_ht, y=-int(self.__display.height) // 2 + bkg_ht // 2, z=4.0)
        self.__text_bkg.set_draw_details(back_shader, [text_bkg_tex])


    def slideshow_is_running(self, pics=None, time_delay = 200.0, fade_time = 10.0, paused=False):
        tm = time.time()
        if pics is not None:
            self.__sbg = self.__sfg # if the first tex_load fails then __sfg might be Null TODO should fn return if None?
            self.__next_tm = tm + time_delay
            self.__name_tm = tm + fade_time + float(self.__show_text_tm) # text starts after slide transition
            new_sfg = self.__tex_load(pics, (self.__display.width, self.__display.height))
            if new_sfg is not None: # this is a possible return value which needs to be caught
                self.__sfg = new_sfg
            self.__alpha = 0.0
            self.__delta_alpha = 1.0 / (self.__fps * fade_time) # delta alpha
            # set the file name as the description
            if self.__show_text_tm > 0.0:
                self.__make_text(pics[0], paused) #TODO only uses text for left of pair
                self.__text.regen()
            else: # could have a NO IMAGES selected and being drawn
                self.__textblock.set_text(text_format="{}".format(" "))
                self.__textblock.colouring.set_colour(alpha=0.0)
                self.__text.regen()

            if self.__sbg is None: # first time through
                self.__sbg = self.__sfg
            self.__slide.set_textures([self.__sfg, self.__sbg])
            self.__slide.unif[45:47] = self.__slide.unif[42:44] # transfer front width and height factors to back
            self.__slide.unif[51:53] = self.__slide.unif[48:50] # transfer front width and height offsets
            wh_rat = (self.__display.width * self.__sfg.iy) / (self.__display.height * self.__sfg.ix)
            if (wh_rat > 1.0 and self.__fit) or (wh_rat <= 1.0 and not self.__fit):
                sz1, sz2, os1, os2 = 42, 43, 48, 49
            else:
                sz1, sz2, os1, os2 = 43, 42, 49, 48
                wh_rat = 1.0 / wh_rat
            self.__slide.unif[sz1] = wh_rat
            self.__slide.unif[sz2] = 1.0
            self.__slide.unif[os1] = (wh_rat - 1.0) * 0.5
            self.__slide.unif[os2] = 0.0
            if self.__kenburns:
                self.__xstep, self.__ystep = (self.__slide.unif[i] * 2.0 / (time_delay - fade_time) for i in (48, 49))
                self.__slide.unif[48] = 0.0
                self.__slide.unif[49] = 0.0
                #self.__kb_up = not self.__kb_up # just go in one direction

        if self.__kenburns and self.__alpha >= 1.0:
            t_factor = time_delay - fade_time - self.__next_tm + tm
            #t_factor = self.__next_tm - tm
            #if self.__kb_up:
            #    t_factor = time_delay - t_factor
            # add exponentially smoothed tweening in case of timing delays etc. to avoid 'jumps'
            self.__slide.unif[48] = self.__slide.unif[48] * 0.95 + self.__xstep * t_factor * 0.05
            self.__slide.unif[49] = self.__slide.unif[49] * 0.95 + self.__ystep * t_factor * 0.05

        if self.__alpha < 1.0: # transition is happening
            self.__alpha += self.__delta_alpha
            if self.__alpha > 1.0:
                self.__alpha = 1.0
            self.__slide.unif[44] = self.__alpha * self.__alpha * (3.0 - 2.0 * self.__alpha)

        if (self.__next_tm - tm) < 5.0 or self.__alpha < 1.0:
            self.__in_transition = True # set __in_transition True a few seconds *before* end of previous slide
        else: # no transition effect safe to update database, resuffle etc
            self.__in_transition = False

        self.__slide.draw()

        if self.__alpha >= 1.0 and tm < self.__name_tm:
            # this sets alpha for the TextBlock from 0 to 1 then back to 0
            dt = (self.__show_text_tm - self.__name_tm + tm + 0.1) / self.__show_text_tm
            ramp_pt = max(4.0, self.__show_text_tm / 4.0)
            alpha = max(0.0, min(1.0, ramp_pt * (self.__alpha- abs(1.0 - 2.0 * dt)))) # cap text alpha at image alpha
            self.__textblock.colouring.set_colour(alpha=alpha)
            self.__text.regen()
            self.__text_bkg.set_alpha(alpha)
            if len(self.__textblock.text_format.strip()) > 0: #only draw background if text there
                self.__text_bkg.draw()


        self.__text.draw()
        return self.__display.loop_running()

    def slideshow_stop(self):
        self.__display.destroy()
