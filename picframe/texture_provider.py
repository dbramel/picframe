import logging
import os
import time

import pi3d
from PIL import ImageFilter, Image

from picframe import get_image_meta, mat_image

import threading


class TextureProvider:

    def __init__(self, config, model):
        self.__logger = logging.getLogger("viewer_display.TextureProvider")
        self.__model = model

        self.__blur_amount = config['blur_amount']
        self.__blur_zoom = max(1.0, config['blur_zoom'])
        self.__blur_edges = config['blur_edges']

        self.__edge_alpha = config['edge_alpha']

        self.__mat_images, self.__mat_images_tol = self.__get_mat_image_control_values(config['mat_images'])
        self.__mat_type = config['mat_type']
        self.__outer_mat_color = config['outer_mat_color']
        self.__inner_mat_color = config['inner_mat_color']
        self.__outer_mat_border = config['outer_mat_border']
        self.__inner_mat_border = config['inner_mat_border']
        self.__outer_mat_use_texture = config['outer_mat_use_texture']
        self.__inner_mat_use_texture = config['inner_mat_use_texture']
        self.__mat_resource_folder = os.path.expanduser(config['mat_resource_folder'])
        self.__display_width = None
        self.__display_height = None
        self.__matter = None

        self.__thread = threading.Thread(target=self.__iterate_files)
        self.__thread.daemon = True
        self.__consumed = threading.Event()

        self.__next_pic = None
        self.__next_attrs = None
        self.__next_tex = None

        self.__thread.start()


    def set_matting_images(self, val): # needs to cope with "true", "ON", 0, "0.2" etc.
        try:
            float_val = float(val)
            if round(float_val, 4) == 0.0: # pixellish over a 4k monitor
                val = "true"
            if round(float_val, 4) == 1.0:
                val = "false"
        except: # ignore exceptions, error handling is done in following function
            pass
        self.__mat_images, self.__mat_images_tol = self.__get_mat_image_control_values(val)

    def get_matting_images(self):
        if self.__mat_images and self.__mat_images_tol > 0:
            return self.__mat_images_tol
        elif self.__mat_images and self.__mat_images_tol == -1:
            return 0
        else:
            return 1


    def set_display(self, display_width, display_height):
        self.__display_width = display_width
        self.__display_height = display_height
        self.__matter = mat_image.MatImage(
            display_size=(display_width, display_height),
            resource_folder=self.__mat_resource_folder,
            mat_type=self.__mat_type,
            outer_mat_color=self.__outer_mat_color,
            inner_mat_color=self.__inner_mat_color,
            outer_mat_border=self.__outer_mat_border,
            inner_mat_border=self.__inner_mat_border,
            outer_mat_use_texture=self.__outer_mat_use_texture,
            inner_mat_use_texture=self.__inner_mat_use_texture)

    def consume(self):
        (attrs, pic, tex) = (self.__next_attrs, self.__next_pic, self.__next_tex)
        # Ensure we can't re-consume the same picture
        (self.__next_attrs, self.__next_pic, self.__next_tex) = (None, None, None)
        self.__consumed.set()  # allow thread to start on the next one
        return attrs, pic, tex

    def __iterate_files(self):
        while True:
            attrs, pics = self.__get_next_file()
            if pics:
                tex = self.__tex_load(pics)
                (self.__next_attrs, self.__next_pic, self.__next_tex) = (attrs, pics, tex)
                # don't do anything until it's been consumed
                self.__consumed.clear()
                self.__consumed.wait()
            else:
                # If we didn't get a picture, wait a bit and try again
                time.sleep(0.5)

    def __get_next_file(self):
        pics = self.__model.get_next_file()
        image_attr = {}
        if pics[0] is None:
            pics = None
        else:
            for key in self.__model.get_model_config()['image_attr']:
                if key == 'PICFRAME GPS':
                    image_attr['latitude'] = pics[0].latitude
                    image_attr['longitude'] = pics[0].longitude
                elif key == 'PICFRAME LOCATION':
                    image_attr['location'] = pics[0].location
                else:
                    field_name = self.__model.EXIF_TO_FIELD[key]
                    image_attr[key] = pics[0].__dict__[field_name]  # TODO nicer using namedtuple for Pic
        return image_attr, pics

    def __tex_load(self, pics):
        size = (self.__display_width, self.__display_height)
        try:
            # Load the image(s) and correct their orientation as necessary
            if pics[0]:
                im = get_image_meta.GetImageMeta.get_image_object(pics[0].fname)
                if im is None:
                    return None
                if pics[0].orientation != 1:
                    im = self.__orientate_image(im, pics[0])

            if pics[1]:
                im2 = get_image_meta.GetImageMeta.get_image_object(pics[1].fname)
                if im2 is None:
                    return None
                if pics[1].orientation != 1:
                     im2 = self.__orientate_image(im2, pics[1])

            screen_aspect, image_aspect, diff_aspect = self.__get_aspect_diff(size, im.size)

            if self.__mat_images and diff_aspect > self.__mat_images_tol:
                if not pics[1]:
                    im = self.__matter.mat_image((im,))
                else:
                    im = self.__matter.mat_image((im, im2))
            else:
                if pics[1]: #i.e portrait pair
                    im = self.__create_image_pair(im, im2)



            (w, h) = im.size
            # no longer allow automatic resize to be turned off - but GL_MAX_TEXTURE_SIZE used by Texture
            #max_dimension = MAX_SIZE # TODO changing MAX_SIZE causes serious crash on linux laptop!
            #if not self.__auto_resize: # turned off for 4K display - will cause issues on RPi before v4
            #    max_dimension = 3840 # TODO check if mipmapping should be turned off with this setting.
            #if w > max_dimension:
            #    im = im.resize((max_dimension, int(h * max_dimension / w)), resample=Image.BICUBIC)
            #elif h > max_dimension:
            #    im = im.resize((int(w * max_dimension / h), max_dimension), resample=Image.BICUBIC)

            screen_aspect, image_aspect, diff_aspect = self.__get_aspect_diff(size, im.size)

            if self.__blur_edges and size:
                if diff_aspect > 0.01:
                    (sc_b, sc_f) = (size[1] / im.size[1], size[0] / im.size[0])
                    if screen_aspect > image_aspect:
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
            tex = pi3d.Texture(im, blend=True, m_repeat=True, free_after_load=True)
            #tex = pi3d.Texture(im, blend=True, m_repeat=True, automatic_resize=config.AUTO_RESIZE,
            #                    mipmap=config.AUTO_RESIZE, free_after_load=True) # poss try this if still some artifacts with full resolution
        except Exception as e:
            self.__logger.warning("Can't create tex from file: \"%s\" or \"%s\"", pics[0].fname, pics[1])
            self.__logger.warning("Cause: %s", e)
            tex = None
            #raise # only re-raise errors here while debugging
        return tex

    def __get_mat_image_control_values(self, mat_images_value):
        on = True
        val = 0.01
        org_val = str(mat_images_value).lower()
        if org_val in ('true', 'yes', 'on'):
            val = -1
        elif org_val in ('false', 'no', 'off'):
            on = False
        else:
            try:
                val = float(org_val)
            except:
                self.__logger.warning("Invalid value for config option 'mat_images'. Using default.")
        return(on, val)


    def __get_aspect_diff(self, screen_size, image_size):
        screen_aspect = screen_size[0] / screen_size[1]
        image_aspect = image_size[0] / image_size[1]

        if screen_aspect > image_aspect:
            diff_aspect = 1 - (image_aspect / screen_aspect)
        else:
            diff_aspect = 1 - (screen_aspect / image_aspect)
        return (screen_aspect, image_aspect, diff_aspect)
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

    def __orientate_image(self, im, pic):
        ext = os.path.splitext(pic.fname)[1].lower()
        if ext  in ('.heif','.heic'): # heif and heic images are converted to PIL.Image obects and are alway in correct orienation
            return im
        orientation = pic.orientation
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
