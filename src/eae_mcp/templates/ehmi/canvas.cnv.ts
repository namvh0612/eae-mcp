/*
 * Created by EcoStruxure Automation Expert.
 * User:    
 * Date: @@DATE@@
 * Time: @@TIME@@
 * 
 */
namespace WEB.Main.Canvases {

  export class @@NAME@@ extends NxtControl.GuiFramework.Canvas {

    /**
     * Type of an object (never change this)
     * @type String
     * @default
     */
    @System.DefaultValue('WEB.Main.Canvases.@@NAME@@')
    protected type: string;
    
	/// ************ DO NOT DELETE THIS METHOD !!!	******///	
    /**
     * Returns {@link WEB.Main.@@NAME@@} instance from an object representation
     * @static
     * @param {Object} object Object to create a symbol from
     * @return {WEB.Main.@@NAME@@} An instance of WEB.Main.@@NAME@@
     */
    static fromObject(element?: HTMLCanvasElement | string, options?: any): @@NAME@@ {
      return <@@NAME@@> new @@NAME@@(element, options);
    }
    
  } 
}
